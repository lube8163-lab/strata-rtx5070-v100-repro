"""Pinned upstream installation; no drivers, registry edits or model uploads."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from .config import LOCK, REPO, ascii_path, gpus, roles, write


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        while data:=source.read(16<<20):
            h.update(data)
    return h.hexdigest()


def checked_download(item,destination,workers=8):
    from .download import download
    destination=Path(destination)
    if destination.exists():
        if destination.stat().st_size!=item['size'] or digest(destination)!=item['sha256']:
            raise RuntimeError('Existing file differs from lock; it was preserved: '+str(destination))
    else:
        download(item['url'],destination,workers=workers,published_size=item['size'],published_sha256=item['sha256'])
    return destination


def extract(archive,destination):
    destination=Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            target=(destination/item.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError('Archive path leaves destination: '+item.filename)
        # Some release archives wrap their binaries in one directory. Keep the
        # executable and adjacent DLLs at the configured root, retaining notices.
        names=[x.filename for x in source.infolist() if not x.is_dir()]
        candidates=[n for n in names if Path(n).name in ('strata.exe','llama-server.exe')]
        prefix=str(Path(candidates[0]).parent).replace('\\','/')+'/' if len(candidates)==1 and '/' in candidates[0] else ''
        for item in source.infolist():
            name=item.filename[len(prefix):] if prefix and item.filename.startswith(prefix) else item.filename
            if not name:
                continue
            target=(destination/name).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError('Normalized archive path leaves destination')
            if item.is_dir():
                target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with source.open(item) as src,target.open('wb') as dst:
                    shutil.copyfileobj(src,dst)


def clone(root):
    destination=root/'Strata'
    if not destination.exists():
        destination.mkdir(parents=True)
        subprocess.run(['git','init',str(destination)],check=True)
        subprocess.run(['git','-C',str(destination),'remote','add','origin',LOCK['strata']['repository']],check=True)
        subprocess.run(['git','-C',str(destination),'fetch','--depth','1','origin',LOCK['strata']['commit']],check=True)
        subprocess.run(['git','-C',str(destination),'checkout','--detach','FETCH_HEAD'],check=True)
    actual=subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD'],text=True).strip()
    if actual!=LOCK['strata']['commit']:
        raise RuntimeError('Existing checkout differs from pinned commit; it was preserved')
    return destination


def install(args):
    if not args.accept_large_download:
        raise RuntimeError('Flash requires75.84GB GGUF plus packs (~120-140GB disk); use --accept-large-download')
    root=ascii_path(args.root)
    root.mkdir(parents=True,exist_ok=True)
    # Upstream accepts a physical nvidia-smi index here; runtime uses UUIDs afterwards.
    main,_=roles(gpus(),args.primary,args.peer)
    model_folder=root/'Strata-data/models/IQ3_XXS'
    remaining=sum(x['size'] for x in LOCK['flash']['files'] if not (model_folder/x['filename']).exists())
    if shutil.disk_usage(root).free < remaining + 50*2**30:
        raise RuntimeError('Keep enough free disk for remaining GGUF and another50GiB of generated packs')
    repo=clone(root)
    python=repo/'.venv/Scripts/python.exe'
    if not python.exists():
        subprocess.run([sys.executable,'-m','venv',str(repo/'.venv')],check=True)
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(repo/'requirements.txt')],check=True)
    if Path(sys.executable).resolve()!=python.resolve():
        subprocess.run([str(python),'-X','utf8','-u',str(REPO/'repro.py'),*sys.argv[1:]],check=True)
        return
    engine=LOCK['strata']['engine']
    archive=checked_download(engine,root/'downloads'/engine['filename'],args.workers)
    extract(archive,repo/'engine-cuda12')
    meta=json.loads((repo/'engine-cuda12/BUILD.json').read_text(encoding='utf-8'))
    if meta.get('version')!=LOCK['strata']['version'] or str(meta.get('cuda'))!='12.9':
        raise RuntimeError('Pinned engine build metadata did not match')
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--upgrade','--target',str(root/'cuda12-runtime'),
                    LOCK['cuda12']['cublas'],LOCK['cuda12']['runtime']],check=True)
    verified=[]
    for item in LOCK['flash']['files']:
        destination=checked_download(item,model_folder/item['filename'],args.workers)
        # This is upstream's own verified marker format. Size/hash were checked above.
        destination.with_name(destination.name+'.done').write_text('sha256 '+item['sha256'],encoding='utf-8')
        verified.append({'filename':item['filename'],'size':item['size'],'sha256':item['sha256']})
    write(root/'verified-flash.json',verified)
    command=[str(python),'-X','utf8','-u',str(REPO/'reprolib/upstream_setup.py'),'--strata-dir',str(repo),
             '--local-settings',str(root/'upstream-settings.json'),'--setup','--yes','--gpu',str(main['index']),
             '--cuda','12','--family','qwen','--model','IQ3_XXS','--context','32768','--vision','no',
             '--low-ram','resident','--resident-budget-gib','12','--vram-reserve-mib','1200',
             '--data-dir',str(root/'Strata-data'),'--port','8097','--no-start',
             '--prebuilt',engine['url'].rsplit('/',1)[0]+'/']
    subprocess.run(command,cwd=repo,check=True)
    print('Pinned Flash model, packs, MTP and CUDA12 runtime prepared. Next run configure.')


def install_rana(args):
    if not args.accept_large_download:
        raise RuntimeError('RANA+MTP+projector download is20.64GB; use --accept-large-download')
    root=ascii_path(args.root)
    root.mkdir(parents=True,exist_ok=True)
    python=root/'Strata/.venv/Scripts/python.exe'
    if not python.is_file():
        raise RuntimeError('Run Flash bootstrap first to prepare the pinned Python environment')
    if Path(sys.executable).resolve()!=python.resolve():
        subprocess.run([str(python),'-X','utf8','-u',str(REPO/'repro.py'),*sys.argv[1:]],check=True)
        return
    if shutil.disk_usage(root).free < 24*2**30:
        raise RuntimeError('RANA preparation needs at least24GiB free disk')
    for item in LOCK['rana']['files']:
        checked_download(item,root/'models/rana'/item['filename'],args.workers)
    for item in LOCK['llama']['assets']:
        archive=checked_download(item,root/'downloads'/item['name'],args.workers)
        extract(archive,root/'llama')
    print('Pinned llama.cpp b10964 and RANA assets prepared.')
