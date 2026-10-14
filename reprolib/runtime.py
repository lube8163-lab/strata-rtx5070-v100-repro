"""Owned server lifecycle and synthetic input helpers."""
from pathlib import Path
import json,sys,subprocess,time,threading,os
import psutil
import socket
import urllib.request
from .telemetry import Sampler

def load_tokenizer(folder):
    import strata_tokenizer as ST
    vocab = json.loads((folder / 'vocab.json').read_text(encoding='utf-8'))
    tokens = [None] * len(vocab)
    for token, index in vocab.items():
        tokens[index] = token
    merges = (folder / 'merges.txt').read_text(encoding='utf-8').split('\n')
    types = json.loads((folder / 'token_type.json').read_text(encoding='utf-8'))
    return ST.Tokenizer(tokens, merges, types)

def needle_prompt(tokenizer, tokens, tag):
    block = 'A normal record describes a quiet garden, a small river, and an ordinary blue notebook.\n'
    per_block = len(tokenizer.encode(block))
    repeats = max(1, (tokens - 256) // per_block)
    target = 'EMBER-2746'
    content = (f'Fresh test {tag}. Read these records and return the value in the SPECIAL_CERTIFICATE record.\n'
               + block * (repeats // 2) + f'\nSPECIAL_CERTIFICATE = {target}\n'
               + block * (repeats - repeats // 2) + '\nReturn only the SPECIAL_CERTIFICATE value.\n')
    return content, target, len(tokenizer.encode(content))

OWNED = {}


def register_owned(process):
    try:
        owner = psutil.Process(process.pid)
        OWNED[id(process)]={'pid':process.pid,'created':owner.create_time(),'children':set()}
    except psutil.Error:
        OWNED[id(process)]={'pid':process.pid,'created':None,'children':set()}


def track_owned(process):
    if id(process) not in OWNED:
        if process.poll() is not None:
            return []
        register_owned(process)
    entry=OWNED[id(process)]
    try:
        owner=psutil.Process(process.pid)
        if entry['created'] is None or owner.create_time()!=entry['created']:
            return []
        children=owner.children(recursive=True)
        entry['children'].update((p.pid,p.create_time()) for p in children)
        return [owner,*children]
    except psutil.Error:
        return []


def stop_owned(process):
    track_owned(process)
    entry=OWNED.get(id(process),{'children':set()})
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    for pid, started in entry['children']:
        try:
            child = psutil.Process(pid)
            if child.create_time() == started:
                child.terminate()
        except psutil.Error:
            pass


def limit_reason(data, low_since, now):
    if any(max(g.get('temperature_c',0),g.get('memory_temperature_c',0))>=80 for g in data['gpus']):
        return 'GPU core or fresh HBM temperature reached80C',low_since
    low_since=(low_since if low_since is not None else now) if data['available_ram_bytes']<2*2**30 else None
    if low_since is not None and now-low_since>=10:
        return 'Available RAM below2GiB for10seconds',low_since
    return None,low_since


def interactive(settings,engine):
    from .config import REPO,flash_config,rana_command,write
    out=REPO/'.local/runs'/('interactive-'+time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,exist_ok=False)
    port=8097 if engine=='flash' else 8098
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',port))==0:
            raise RuntimeError(f'Port{port} is occupied; its process was preserved')
    if engine=='flash':
        cfg=flash_config(settings,out)
        write(out/'config.json',cfg)
        command=[sys.executable,'-X','utf8','-u',str(Path(settings['strata_dir'])/'serve/server.py'),
                 '--engine','strata','--config',str(out/'config.json'),'--port',str(port)]
        env=dict(os.environ)
        cwd=settings['strata_dir']
    else:
        # Enable request-controlled Thinking on the installed 27B web/API server.
        command=rana_command(settings,thinking=True)
        env={**os.environ,'CUDA_VISIBLE_DEVICES':settings['peer']['uuid']}
        cwd=str(Path(settings['llama_exe']).parent)
    write(out/'command.json',command)
    log=(out/'server.log').open('w',encoding='utf-8')
    process=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    register_owned(process)
    stopped=threading.Event()
    guard={'reason':None}
    phase={'name':'loading'}
    def monitor():
        sensors=Sampler()
        if sensors.error:
            guard['reason']='Live NVML monitoring unavailable: '+sensors.error
            stop_owned(process)
            return
        low=None
        with (out/'telemetry.jsonl').open('w',encoding='utf-8') as target:
            while not stopped.is_set():
                data=sensors.sample(phase['name'])
                track_owned(process)
                target.write(json.dumps(data,ensure_ascii=False)+'\n')
                target.flush()
                reason,low=limit_reason(data,low,time.monotonic())
                if reason:
                    guard['reason']=reason
                    stop_owned(process)
                    return
                stopped.wait(0.5)
    thread=threading.Thread(target=monitor,daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+900
        ready=False
        print('Loading. Logs: '+str(out),flush=True)
        while time.monotonic()<deadline and process.poll() is None:
            try:
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(f'http://127.0.0.1:{port}/health',timeout=2) as response:
                    if json.load(response).get('status') in ('ok','ready'):
                        ready=True
                        break
            except Exception:
                pass
            time.sleep(1)
        if not ready:
            raise RuntimeError(guard['reason'] or 'Server exited or did not become ready; inspect server.log')
        phase['name']='interactive'
        print(f'Open http://127.0.0.1:{port}/ ; API /v1 ; Ctrl+C stops this owned server',flush=True)
        process.wait()
        if guard['reason']:
            raise RuntimeError(guard['reason'])
        return process.returncode
    except KeyboardInterrupt:
        return 0
    finally:
        stop_owned(process)
        stopped.set()
        thread.join(timeout=5)
        log.close()
        write(out/'ended.json',{'guard_stop':guard['reason'],'returncode':process.poll()})

