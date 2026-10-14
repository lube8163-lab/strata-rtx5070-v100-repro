"""Resumable range download of the pinned public GGUF, verified against its LFS hash."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import os
import re
import threading
import time

import requests

SEGMENT = 64 << 20

def download(url, destination, workers=16, published_size=None, published_sha256=None, segment_size=SEGMENT):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if published_size is None:
        response = requests.head(url, allow_redirects=False, timeout=60)
        response.raise_for_status()
        total = int(response.headers.get('X-Linked-Size', response.headers.get('Content-Length', '0')))
        expected = response.headers.get('X-Linked-Etag', '').strip('"')
    else:
        total, expected = int(published_size), published_sha256
    if total <= 0 or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise RuntimeError('Published file size and SHA-256 are required')
    segment = int(segment_size)
    part = destination.with_name(destination.name + '.part')
    state_path = destination.with_name(destination.name + '.ranges.json')
    count = (total + segment - 1) // segment
    lock = threading.Lock()
    began = time.perf_counter()
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if (state['total'], state['sha256'], state['segment']) != (total, expected, segment):
            raise RuntimeError('Range manifest does not match this pinned model')
        if not part.exists():
            raise RuntimeError('Range manifest exists without its partial file')
    else:
        have = part.stat().st_size if part.exists() else 0
        if have > total:
            raise RuntimeError('Existing partial file is larger than the model')
        state = {'url': url, 'total': total, 'sha256': expected, 'segment': segment,
                 'completed': list(range(have // segment)), 'resumed_prefix_bytes': have}
    completed = set(state['completed'])

    def save():
        state['completed'] = sorted(completed)
        temporary = state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(state), encoding='utf-8')
        temporary.replace(state_path)

    save()  # record the valid prefix before preallocation changes the file length
    with open(part, 'r+b' if part.exists() else 'w+b') as output:
        output.truncate(total)
    start_bytes = sum(min(segment, total - i * segment) for i in completed)
    print(f'  Range download: {workers} workers, resume {start_bytes / 1e9:.2f}/{total / 1e9:.2f} GB', flush=True)

    def fetch(index):
        start = index * segment
        end = min(total, start + segment) - 1
        for attempt in range(6):
            try:
                with requests.get(url, headers={'Range': f'bytes={start}-{end}',
                                               'Accept-Encoding': 'identity'},
                                  stream=True, timeout=(30, 90)) as incoming:
                    incoming.raise_for_status()
                    if incoming.status_code != 206 or incoming.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
                        raise RuntimeError('Server did not honor the exact requested range')
                    got = 0
                    with open(part, 'r+b', buffering=0) as output:
                        output.seek(start)
                        for data in incoming.iter_content(1 << 20):
                            if not data:
                                continue
                            if got + len(data) > end - start + 1:
                                raise RuntimeError('Range exceeds expected length')
                            output.write(data)
                            got += len(data)
                        if got != end - start + 1:
                            raise RuntimeError('Range ended early')
                        os.fsync(output.fileno())
                return index
            except (requests.RequestException, OSError):
                if attempt == 5:
                    raise
                time.sleep(min(3 * (attempt + 1), 15))

    last = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, i) for i in range(count) if i not in completed]
        for future in as_completed(futures):
            completed.add(future.result())
            with lock:
                save()
            now = time.perf_counter()
            if now - last >= 10 or len(completed) == count:
                done = sum(min(segment, total - i * segment) for i in completed)
                speed = (done - start_bytes) / max(now - began, 0.01) / 1048576
                print(f'  {destination.name}: {done / 1e9:.2f}/{total / 1e9:.2f} GB, {speed:.1f} MiB/s', flush=True)
                last = now
    print(f'  Verifying published SHA-256: {destination.name}', flush=True)
    actual = hashlib.sha256()
    with open(part, 'rb') as source:
        while data := source.read(16 << 20):
            actual.update(data)
    if actual.hexdigest() != expected:
        raise RuntimeError('Downloaded model SHA-256 mismatch; partial file retained for investigation')
    part.replace(destination)
    state['verified_sha256'] = actual.hexdigest()
    state['seconds'] = time.perf_counter() - began
    state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    print(f'  SHA-256 passed: {destination.name}', flush=True)
    return total, expected
