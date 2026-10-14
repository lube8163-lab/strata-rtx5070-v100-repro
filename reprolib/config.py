"""Build local settings from discovered GPUs and user-selected directories."""
import csv
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
LOCAL = REPO / '.local/runtime.json'
LOCK = json.loads((REPO / 'models.lock.json').read_text(encoding='utf-8'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def ascii_path(path):
    value = Path(path).expanduser().absolute()
    if not str(value).isascii():
        raise ValueError('Use an ASCII path (for example C:/AI/strata-repro); see docs/troubleshooting.md')
    return value


def memory():
    class Status(ctypes.Structure):
        _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong)] + [
            (x,ctypes.c_ulonglong) for x in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    status=Status()
    status.length=ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError('GlobalMemoryStatusEx failed')
    return {'total_gib':status.total/2**30,'available_gib':status.available/2**30}


def gpus():
    fields=['index','uuid','name','driver_version','memory.total','memory.free']
    p=subprocess.run(['nvidia-smi','--query-gpu='+','.join(fields),'--format=csv,noheader,nounits'],
                     capture_output=True,text=True,check=True,timeout=15)
    rows=[]
    for row in csv.reader(io.StringIO(p.stdout)):
        item=dict(zip(fields,[x.strip() for x in row]))
        item['index']=int(item['index'])
        item['memory.total']=int(item['memory.total'])
        item['memory.free']=int(item['memory.free'])
        rows.append(item)
    return rows


def choose(rows, pattern, selector=None):
    matches=[x for x in rows if (str(x['index'])==selector or x['uuid']==selector)] if selector is not None else [x for x in rows if pattern in x['name']]
    if len(matches)!=1:
        raise ValueError(f'Expected one {pattern} GPU; use --primary/--peer to select its nvidia-smi index or UUID')
    return matches[0]


def roles(rows, primary=None, peer=None):
    main=choose(rows,'RTX 5070',primary)
    extra=choose(rows,'V100',peer)
    if main['uuid']==extra['uuid']:
        raise ValueError('Primary and peer must be different GPUs')
    if main['driver_version']!=extra['driver_version']:
        raise ValueError('Both GPUs need a working common NVIDIA driver; no driver changes are automated')
    if main['memory.total']<11000 or extra['memory.total']<30000:
        raise ValueError('Measured profile requires >=11,000 MiB primary and >=30,000 MiB peer VRAM')
    return main,extra


def build_settings(args):
    root=ascii_path(args.root)
    main,peer=roles(gpus(),args.primary,args.peer)
    settings={'root':str(root),'primary':main,'peer':peer,'context':args.context,
              'strata_dir':str(ascii_path(args.strata_dir or root/'Strata')),
              'data_dir':str(ascii_path(args.data_dir or root/'Strata-data')),
              'cuda_dir':str(ascii_path(args.cuda_dir or root/'cuda12-runtime')),
              'python':None,'thermal_limit_c':80,'ram_limit_gib':2,'ram_limit_seconds':10}
    settings['python']=str(Path(settings['strata_dir'])/'.venv/Scripts/python.exe')
    rana=root/'models/rana'
    settings['rana_model']=str(ascii_path(args.rana_model or rana/LOCK['rana']['files'][0]['filename']))
    settings['rana_mtp']=str(ascii_path(args.rana_mtp or rana/LOCK['rana']['files'][1]['filename']))
    settings['rana_mmproj']=str(ascii_path(args.rana_mmproj or rana/LOCK['rana']['files'][2]['filename']))
    settings['llama_exe']=str(ascii_path(args.llama_exe or root/'llama/llama-server.exe'))
    return settings


def load():
    return json.loads(LOCAL.read_text(encoding='utf-8'))


def validate(settings, engine='flash'):
    if os.name!='nt':
        raise RuntimeError('This measured recipe targets Windows; Linux was not validated')
    if memory()['total_gib']<30:
        raise RuntimeError('The measured resident profile requires a32GB-class PC')
    rows=gpus()
    roles(rows,settings['primary']['uuid'],settings['peer']['uuid'])
    if engine=='flash':
        repo=Path(settings['strata_dir'])
        commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        if commit!=LOCK['strata']['commit']:
            raise RuntimeError('Strata checkout differs from the measured pinned commit')
        meta=json.loads((repo/'engine-cuda12/BUILD.json').read_text(encoding='utf-8'))
        if meta.get('version')!=LOCK['strata']['version'] or not meta.get('ptx') or 70 not in meta.get('archs',[]):
            raise RuntimeError('Expected pinned CUDA12 engine with sm70 and PTX')
        if str(meta.get('cuda'))!='12.9':
            raise RuntimeError('Expected measured CUDA12.9 engine')
        data=Path(settings['data_dir'])
        required=[repo/'engine-cuda12/strata.exe',repo/'data/expert-profile.bin',
                  data/'packs/iq3_xxs/dense.bin',data/'packs/iq3_xxs/experts.bin',
                  data/'packs/iq3_xxs/tokenizer/vocab.json',data/'mtp/rt/dense.bin',data/'mtp/rt/experts.bin',
                  Path(settings['cuda_dir'])/'nvidia/cublas/bin/cublas64_12.dll',
                  Path(settings['cuda_dir'])/'nvidia/cuda_runtime/bin/cudart64_12.dll']
        for item in LOCK['flash']['files']:
            target=data/'models/IQ3_XXS'/item['filename']
            required.append(target)
            if target.is_file() and target.stat().st_size!=item['size']:
                raise RuntimeError('Unexpected model size: '+str(target))
    else:
        required=[Path(settings[x]) for x in ('llama_exe','rana_model','rana_mtp','rana_mmproj')]
        for key,item in zip(('rana_model','rana_mtp','rana_mmproj'),LOCK['rana']['files']):
            if Path(settings[key]).is_file() and Path(settings[key]).stat().st_size!=item['size']:
                raise RuntimeError('Unexpected RANA asset size: '+settings[key])
    missing=[str(p) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError('Missing prepared files: '+', '.join(missing))


def flash_config(s,out,context=None):
    repo,data=Path(s['strata_dir']),Path(s['data_dir'])
    native=[data/'models/IQ3_XXS'/x['filename'] for x in LOCK['flash']['files']]
    return {'exe':str(repo/'engine-cuda12/strata.exe'),'cwd':str(repo),
            'args':['--pack',str(data/'packs/iq3_xxs'),'--native',str(native[0]),'--ple-gguf',str(native[1]),
                    '--expert-profile',str(repo/'data/expert-profile.bin'),'--expert-cache','auto',
                    '--prefill','1024','--spec','4','--spec-min-p','0.5','--mtp',str(data/'mtp/rt'),
                    '--max-context',str(context or s['context']),'--kv','int8','--resident-experts',
                    '--vram-reserve-mib','1200','--resident-budget-gib','12','--stats',
                    '--peer-device','1','--peer-reserve-mib','1024','--peer-prefill-rows','10240'],
            'tokenizer':str(data/'packs/iq3_xxs/tokenizer'),'log':str(Path(out)/'engine.log'),
            'gpu':[s['primary']['index'],s['peer']['index']],'gpus_asked':True,'cuda':12,
            'lib_dirs':[str(Path(s['cuda_dir'])/'nvidia/cublas/bin'),str(Path(s['cuda_dir'])/'nvidia/cuda_runtime/bin')],
            'host':'127.0.0.1','port':8097,'open_browser':False,'model_name':'local-agent',
            'env':{'CUDA_VISIBLE_DEVICES':s['primary']['uuid']+','+s['peer']['uuid'],
                   'STRATA_RESIDENT_HEADROOM_GIB':'6','STRATA_PREFILL_TIMING':'1','STRATA_DECODE_TIMING':'1'}}


def rana_command(s,thinking=False):
    command=[s['llama_exe'],'--model',s['rana_model'],'--model-draft',s['rana_mtp'],
             '--mmproj',s['rana_mmproj'],'--mmproj-device','CUDA0','--image-max-tokens','2048',
             '--alias','local-agent','--host','127.0.0.1','--port','8098','--device','CUDA0',
             '--split-mode','none','--main-gpu','0','--n-gpu-layers','99','--ctx-size',str(s['context']),
             '--batch-size','1024','--ubatch-size','512','--parallel','1','--threads','8',
             '--flash-attn','on','--cache-type-k','f16','--cache-type-v','f16','--fit','off','--jinja',
             '--reasoning','on' if thinking else 'off','--metrics','--cache-ram','0','--load-mode','none',
             '--verbosity','4','--spec-type','draft-mtp','--spec-draft-n-max','3','--spec-draft-n-min','0',
             '--spec-draft-p-min','0.0','--spec-draft-ngl','all','--spec-draft-device','CUDA0',
             '--spec-draft-type-k','f16','--spec-draft-type-v','f16',
             '--temp','0.7','--top-p','0.8','--top-k','20','--min-p','0','--presence-penalty','0','--repeat-penalty','1.0']
    if thinking:
        command+=['--reasoning-format','deepseek','--reasoning-budget','1536']
    return command
