"""Windows check/setup/configure/run/benchmark entry point."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from reprolib.config import REPO,LOCAL,LOCK,write,gpus,roles,memory,build_settings,load,validate


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    check=sub.add_parser('check',help='Read current GPU/driver/RAM state; changes nothing')
    check.add_argument('--primary')
    check.add_argument('--peer')
    for name in ('bootstrap','bootstrap-rana'):
        p=sub.add_parser(name)
        p.add_argument('--root',required=True)
        p.add_argument('--accept-large-download',action='store_true')
        p.add_argument('--workers',type=int,default=8,choices=range(1,33))
        p.add_argument('--primary')
        p.add_argument('--peer')
    p=sub.add_parser('configure',help='Write ignored, PC-specific settings; no downloads or server starts')
    p.add_argument('--root',required=True)
    p.add_argument('--context',type=int,choices=(32768,131072),default=32768)
    for option in ('primary','peer','strata-dir','data-dir','cuda-dir','rana-model','rana-mtp','rana-mmproj','llama-exe'):
        p.add_argument('--'+option)
    p=sub.add_parser('run')
    p.add_argument('--engine',choices=('flash','rana'),default='flash')
    p.add_argument('--context',type=int,choices=(32768,131072))
    p=sub.add_parser('benchmark')
    p.add_argument('--engines',nargs='+',choices=('rana','flash'),default=['rana','flash'])
    p.add_argument('--thinking',action='store_true',help='Add the same four Thinking ON diagnostic tasks')
    p.add_argument('--smoke',action='store_true',help='One generation per engine; no quality or throughput conclusion')
    p=sub.add_parser('verify',help='Rehash configured model files against models.lock.json (can take minutes)')
    p.add_argument('--engine',choices=('flash','rana'),default='flash')
    return ap


def in_venv(s,args):
    target=Path(s['python'])
    if not target.is_file():
        raise RuntimeError('Strata/.venv is missing; run bootstrap or configure a prepared Strata folder')
    if Path(sys.executable).resolve()!=target.resolve():
        return subprocess.call([str(target),'-X','utf8','-u',str(REPO/'repro.py'),*args])
    return None


def main():
    args=parser().parse_args()
    if not str(REPO).isascii():
        raise RuntimeError('Clone this repository into an ASCII path; see docs/troubleshooting.md')
    if os.name!='nt':
        raise RuntimeError('Measured recipe is Windows-only; Linux reproduction has not been tested')
    if sys.version_info<(3,12):
        raise RuntimeError('Use Python3.12 or newer')
    if args.command=='check':
        rows=gpus()
        main_gpu,peer_gpu=roles(rows,args.primary,args.peer)
        ram=memory()
        if ram['total_gib']<30:
            raise RuntimeError('Measured resident profile needs a32GB-class Windows PC')
        print(json.dumps({'gpus':rows,'primary':main_gpu['name'],'peer':peer_gpu['name'],'ram':ram,
                          'driver_changes':'none','expected_profile':'CUDA12.9, sm70+PTX, peer-prefill10240'},indent=2))
        return 0
    if args.command in ('bootstrap','bootstrap-rana'):
        from reprolib.bootstrap import install,install_rana
        (install if args.command=='bootstrap' else install_rana)(args)
        return 0
    if args.command=='configure':
        settings=build_settings(args)
        write(LOCAL,settings)
        print('Wrote '+str(LOCAL)+' (ignored by Git). Runtime paths and GPU UUIDs stay local.')
        return 0
    settings=load()
    status=in_venv(settings,sys.argv[1:])
    if status is not None:
        return status
    if args.command=='verify':
        from reprolib.bootstrap import digest
        validate(settings,args.engine)
        for key,item in zip(('rana_model','rana_mtp','rana_mmproj'),LOCK['rana']['files']) if args.engine=='rana' else [(None,x) for x in LOCK['flash']['files']]:
            path=Path(settings[key]) if key else Path(settings['data_dir'])/'models/IQ3_XXS'/item['filename']
            if digest(path)!=item['sha256']:
                raise RuntimeError('SHA256 mismatch; file preserved: '+str(path))
            print('SHA256 verified: '+item['filename'])
        return 0
    if args.command=='run':
        validate(settings,args.engine)
        if args.context:
            settings['context']=args.context
        if args.engine=='rana' and settings['context']!=32768:
            raise RuntimeError('This recipe only validated RANA at32K;128K run profile is Flash-only')
        from reprolib.runtime import interactive
        return interactive(settings,args.engine)
    for engine in args.engines:
        validate(settings,engine)
    if settings['context']!=32768 and not args.smoke:
        raise RuntimeError('Main comparison uses32K. Reconfigure --context32768 before benchmarking')
    folder=REPO/'.local/runs'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True,exist_ok=False)
    write(folder/'hardware.json',{'gpus':gpus(),'ram':memory(),'logical_cpu_count':os.cpu_count(),'python':sys.version,
                                'smoke':args.smoke,'thinking':args.thinking})
    env={**os.environ,'STRATA_REPRO_RUN_DIR':str(folder)}
    for engine in args.engines:
        command=[sys.executable,'-X','utf8','-u','-m','reprolib.benchmark','--model',engine]
        if args.smoke:
            command+=['--smoke']
        elif args.thinking and engine=='flash':
            command+=['--include-thinking']
        subprocess.run(command,cwd=REPO,env=env,check=True)
        if args.thinking and engine=='rana' and not args.smoke:
            subprocess.run([sys.executable,'-X','utf8','-u','-m','reprolib.benchmark','--model','rana','--thinking'],cwd=REPO,env=env,check=True)
    subprocess.run([sys.executable,'-X','utf8','-u','-m','reprolib.summarize',str(folder)],cwd=REPO,check=True)
    print('Saved '+str(folder))
    return 0


if __name__=='__main__':
    try:
        sys.exit(main())
    except (RuntimeError,ValueError,OSError,subprocess.CalledProcessError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
