"""Sequential, owned local servers; identical fixtures and synchronized telemetry."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import psutil
from reprolib.evaluation import QUALITY, SPEED, REVIEW, grade

REPO = Path(__file__).resolve().parent.parent
from reprolib.config import load, flash_config, rana_command, write as config_write
SETTINGS = load()
ROOT = Path(os.environ['STRATA_REPRO_RUN_DIR'])
ROOT.mkdir(parents=True,exist_ok=True)
STRATA_DIR = Path(SETTINGS['strata_dir'])
from reprolib.telemetry import Sampler
from reprolib.runtime import stop_owned, register_owned, track_owned, load_tokenizer, needle_prompt
sys.path.insert(0, str(STRATA_DIR / 'tools'))
import strata_bench as B
V100 = SETTINGS['peer']['uuid']
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def get_json(base, route, timeout=5):
    with OPENER.open(base + route, timeout=timeout) as response:
        return json.load(response)


def stream(base, prompt, max_tokens, thinking=False, thinking_effort='high'):
    body = {'model': 'local-agent', 'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0, 'top_p': 1.0, 'top_k': 1, 'min_p': 0,
            'presence_penalty': 0, 'frequency_penalty': 0, 'repeat_penalty': 1.0,
            'seed': 1234, 'max_tokens': max_tokens, 'cache_prompt': False,
            'reasoning_effort': 'none', 'chat_template_kwargs': {'enable_thinking': False},
            'stream': True, 'stream_options': {'include_usage': True}}
    if thinking:
        body.update(reasoning_effort=thinking_effort, chat_template_kwargs={'enable_thinking': True},
                    reasoning_budget_tokens=1536)
    req = urllib.request.Request(base + '/v1/chat/completions',
                                 data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.perf_counter()
    first = None
    text = ''
    usage = {}
    timings = {}
    finish = None
    tail = []
    error = None
    reasoning = ''
    try:
        with OPENER.open(req, timeout=600) as response:
            for raw in response:
                line = raw.decode('utf-8').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                item = json.loads(data)
                if item.get('error'):
                    error = item['error']
                for choice in item.get('choices', []):
                    delta = choice.get('delta', {})
                    piece = delta.get('content') or ''
                    reason = delta.get('reasoning_content') or ''
                    if (piece or reason) and first is None:
                        first = time.perf_counter() - t0
                    text += piece
                    reasoning += reason
                    finish = choice.get('finish_reason') or finish
                usage = item.get('usage') or usage
                timings = item.get('timings') or timings
                if item.get('usage') or item.get('timings') or any(c.get('finish_reason') for c in item.get('choices', [])):
                    tail.append(item)
    except urllib.error.HTTPError as exc:
        error = str(exc) + ': ' + exc.read().decode('utf-8','replace')[:2000]
    except Exception as exc:
        error = str(exc)
    result = dict(first=first, total=time.perf_counter()-t0, text=text, reasoning=reasoning,
                  usage=usage, timings=timings, finish=finish, err=error, stream_tail=tail[-3:],
                  settings={k:v for k,v in body.items() if k!='messages'})
    return result


def server_spec(model, out, thinking=False):
    if model == 'rana':
        command=rana_command(SETTINGS,thinking)
        write(out/'model-config.json', {'command':command,'context':SETTINGS['context'],
              'cuda_visible_devices':V100,'vision_projector_loaded':True,
              'ram_tuning':'--cache-ram 0 --load-mode none'})
        return command,{**os.environ,'CUDA_VISIBLE_DEVICES':V100},Path(SETTINGS['llama_exe']).parent,8098
    cfg=flash_config(SETTINGS,out)
    write(out/'model-config.json',cfg)
    command=[sys.executable,'-X','utf8','-u',str(STRATA_DIR/'serve/server.py'),
             '--engine','strata','--config',str(out/'model-config.json'),'--port','8097']
    return command,dict(os.environ),STRATA_DIR,8097


def run(model, fixture, thinking=False, include_thinking=False, output_name=None, smoke=False):
    out = ROOT / (output_name or (model+'-thinking' if thinking else model))
    out.mkdir(exist_ok=False)
    command, env, cwd, port = server_spec(model, out, thinking or include_thinking)
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise RuntimeError(f'Port {port} is occupied; existing process was preserved')
    write(out / 'command.json', command)
    log = (out / 'server.log').open('w', encoding='utf-8')
    sensors = Sampler()
    phase = {'name': 'baseline'}
    stop = threading.Event()
    guard = {'reason': None}
    holder = {'process': None}
    known = {}
    def monitor():
        if sensors.error:
            guard['reason']='Live NVML monitoring unavailable: '+sensors.error
            if holder['process'] is not None:
                stop_owned(holder['process'])
            return
        low_since = None
        psutil.cpu_percent()
        with (out / 'telemetry.jsonl').open('w', encoding='utf-8') as target:
            while not stop.is_set():
                data = sensors.sample(phase['name'])
                data['host_cpu_percent'] = psutil.cpu_percent()
                data['host_ram_used_bytes'] = psutil.virtual_memory().used
                data['owned_processes'] = []
                process = holder['process']
                if process is not None:
                    try:
                        for child in track_owned(process):
                            key = (child.pid, child.create_time())
                            proc = known.setdefault(key, child)
                            memory = proc.memory_info()
                            io = proc.io_counters()
                            data['owned_processes'].append(dict(pid=proc.pid, name=proc.name(),
                                rss_bytes=memory.rss, private_bytes=getattr(memory, 'private', None),
                                cpu_percent_one_core=proc.cpu_percent(), io_read_bytes=io.read_bytes,
                                io_write_bytes=io.write_bytes))
                    except psutil.Error:
                        pass
                target.write(json.dumps(data, ensure_ascii=False)+'\n')
                target.flush()
                if data['available_ram_bytes'] < 2*2**30:
                    low_since = low_since or time.monotonic()
                else:
                    low_since = None
                if low_since is not None and time.monotonic()-low_since >= 10:
                    guard['reason'] = 'RAM availability below 2 GiB for 10 seconds'
                if any(max(g.get('temperature_c',0),g.get('memory_temperature_c',0)) >= 80 for g in data['gpus']):
                    guard['reason'] = 'GPU core or fresh HBM temperature >=80 C'
                if guard['reason'] and process is not None:
                    stop_owned(process)
                    return
                stop.wait(0.5)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    result = dict(model=model, started=datetime.datetime.now().isoformat(), ready=False,
                  completed=False, thinking=thinking, logical_cpu_count=os.cpu_count(), requests=[], fixture_sha256=hashlib.sha256(
                      (ROOT/'fixtures.json').read_bytes()).hexdigest(), sample_period_seconds=0.5)
    write(out/'results.json', result)
    process = None
    try:
        print(f'{model}: idle baseline for 8 seconds', flush=True)
        time.sleep(8)
        if guard['reason']:
            raise RuntimeError(guard['reason'])
        phase['name'] = 'loading'
        t0 = time.monotonic()
        process = subprocess.Popen(command, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        register_owned(process)
        holder['process'] = process
        write(out/'owner.json', dict(pid=process.pid, create_time=psutil.Process(process.pid).create_time()))
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic()+900
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Owned server exited during loading; see server.log')
            try:
                health = get_json(base, '/health')
                if health.get('status') in ('ok','ready'):
                    result['health'] = health
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Readiness timeout after 900 seconds')
        result.update(ready=True, load_seconds=time.monotonic()-t0)
        if model == 'rana':
            result['props'] = get_json(base, '/props')
        write(out/'results.json', result)
        print(f'{model}: ready in {result["load_seconds"]:.1f}s', flush=True)
        def request(task, group, prefix):
            label = f'{group}:{task["name"]}:{prefix}'
            phase['name'] = label
            prompt = f'独立した評価リクエスト {prefix}。\n'+task['prompt']
            before = psutil.disk_io_counters().read_bytes
            start = time.time()
            answer = stream(base, prompt, task['max_tokens'], thinking=(thinking and group=='quality') or group=='thinking',
                            thinking_effort='xhigh' if model=='rana' else 'high')
            digest = B.digest(answer)
            item = dict(name=task['name'], category=task['category'], group=group, prefix=prefix,
                        prompt=prompt, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                        started=start, ended=time.time(), **digest, answer=answer,
                        character_count=len(answer['text']),
                        host_disk_reads_bytes_delta=psutil.disk_io_counters().read_bytes-before)
            if model == 'flash':
                item['metrics'] = get_json(base, '/metrics?requests=all')
            if group in ('quality','thinking'):
                item['grade'] = grade(task, answer['text'], answer['finish'])
            result['requests'].append(item)
            write(out/'results.json', result)
            print(json.dumps(dict(label=label, decode_tps=item['decode_tps'], ttft=item['ttft_s'],
                                  prompt_n=item['read_tokens'], reused=item['reused_tokens'],
                                  finish=answer['finish'], passed=item.get('grade',{}).get('passed')),
                             ensure_ascii=False), flush=True)
            if answer['err'] or not item['ok']:
                raise RuntimeError('Request failed: '+str(answer['err']))
            return item
        request(dict(name='warmup', category='warmup', prompt='日本語で一言だけ挨拶してください。',
                     max_tokens=32), 'warmup', 'W000')
        phase['name'] = 'ready_idle'
        time.sleep(5)
        if smoke:
            request(dict(name='smoke_math',category='smoke',prompt='37 × 29 の答えだけを整数で返してください。',
                         expected=1073,code=None,max_tokens=32),'quality','SMOKE001')
        elif thinking:
            diagnostic=json.loads((ROOT/'thinking-fixtures.json').read_text(encoding='utf-8'))
            result['thinking_fixture_sha256']=hashlib.sha256((ROOT/'thinking-fixtures.json').read_bytes()).hexdigest()
            for j, task in enumerate(diagnostic['tasks']):
                request(task,'quality',f'T{j:03}')
        else:
            for repeat in range(3):
                for j, task in enumerate(fixture['speed']):
                    request(task, 'speed', f'S{repeat}{j}00')
            for j, task in enumerate(fixture['quality']):
                request(task, 'quality', f'Q{j:03}')
            for j, task in enumerate(fixture['review']):
                request(task, 'review', f'R{j:03}')
            for j, task in enumerate(fixture['long']):
                item = request(task, 'long', f'L{j:03}')
                item['grade'] = grade(task, item['answer']['text'], item['answer']['finish'])
                write(out/'results.json', result)
        if include_thinking:
            diagnostic=json.loads((ROOT/'thinking-fixtures.json').read_text(encoding='utf-8'))
            result['thinking_fixture_sha256']=hashlib.sha256((ROOT/'thinking-fixtures.json').read_bytes()).hexdigest()
            for j, task in enumerate(diagnostic['tasks']):
                request(task,'thinking',f'T{j:03}')
        phase['name'] = 'finished_idle'
        time.sleep(5)
        result['completed'] = True
    except Exception as exc:
        result['error'] = str(exc)
        print(f'{model}: {exc}', flush=True)
    finally:
        result['guard_stop'] = guard['reason']
        result['ended'] = datetime.datetime.now().isoformat()
        write(out/'results.json', result)
        phase['name'] = 'shutdown'
        if process is not None:
            stop_owned(process)
        time.sleep(2)
        stop.set()
        thread.join(timeout=5)
        log.close()
        print(f'{model}: saved {out}', flush=True)
    return result['completed']


def prepare():
    import shutil
    for name in ('fixtures.json','thinking-fixtures.json'):
        destination=ROOT/name
        if not destination.exists():
            shutil.copyfile(REPO/'fixtures'/name,destination)
    return json.loads((ROOT/'fixtures.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['rana','flash'])
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--thinking', action='store_true')
    parser.add_argument('--include-thinking', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--output-name', help='Fresh subfolder name for a repeat; existing results are preserved')
    args = parser.parse_args()
    if args.output_name and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}',args.output_name):
        parser.error('output-name must be a simple ASCII subfolder name')
    fixture = prepare()
    if args.prepare:
        print(f'Fixed {len(fixture["quality"])} scored tasks, 2 speed tasks x3, 2 long tasks', flush=True)
    elif args.model:
        sys.exit(0 if run(args.model, fixture, args.thinking, args.include_thinking, args.output_name, args.smoke) else 1)
    else:
        parser.error('--model or --prepare required')
