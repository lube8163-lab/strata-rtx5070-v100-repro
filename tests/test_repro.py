import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
import zipfile

from reprolib.config import roles,ascii_path,flash_config,rana_command,LOCK
from reprolib.bootstrap import extract
from reprolib.download import download
from reprolib.runtime import limit_reason,register_owned,stop_owned,OWNED


def settings():
    return {'primary':{'index':3,'uuid':'PRIMARY'},'peer':{'index':1,'uuid':'PEER'},
            'strata_dir':'C:/AI/Strata','data_dir':'C:/AI/Strata-data','cuda_dir':'C:/AI/cuda12-runtime',
            'context':32768,'llama_exe':'C:/AI/llama/llama-server.exe','rana_model':'C:/AI/model.gguf',
            'rana_mtp':'C:/AI/mtp.gguf','rana_mmproj':'C:/AI/mmproj.gguf'}


class ConfigTests(unittest.TestCase):
    def test_roles_follow_uuid_not_enumeration_order(self):
        rows=[{'index':1,'uuid':'PEER','name':'Tesla V100','driver_version':'581.80','memory.total':32768},
              {'index':3,'uuid':'PRIMARY','name':'RTX 5070','driver_version':'581.80','memory.total':12227}]
        a,b=roles(rows)
        self.assertEqual((a['uuid'],b['uuid']),('PRIMARY','PEER'))
        self.assertEqual(roles(rows,'PRIMARY','PEER'),(a,b))

    def test_driver_mismatch_and_small_peer_refused(self):
        rows=[{'index':0,'uuid':'A','name':'RTX 5070','driver_version':'610.88','memory.total':12227},
              {'index':1,'uuid':'B','name':'V100','driver_version':'581.80','memory.total':32768}]
        with self.assertRaises(ValueError): roles(rows)
        rows[0]['driver_version']='581.80'
        rows[1]['memory.total']=16384
        with self.assertRaises(ValueError): roles(rows)

    def test_non_ascii_data_path_refused(self):
        with self.assertRaises(ValueError): ascii_path('C:/AI/日本語')

    def test_peer_recipe_and_128k_context(self):
        cfg=flash_config(settings(),Path('out'),131072)
        args=cfg['args']
        self.assertEqual(cfg['env']['CUDA_VISIBLE_DEVICES'],'PRIMARY,PEER')
        self.assertEqual(args[args.index('--peer-device')+1],'1')
        self.assertNotIn('--layer-split',args)
        self.assertNotIn('--expert-cache-device1',args)
        self.assertEqual(args[args.index('--max-context')+1],'131072')
        self.assertEqual(cfg['host'],'127.0.0.1')

    def test_rana_ram_and_thinking_recipe(self):
        args=rana_command(settings(),True)
        self.assertEqual(args[args.index('--cache-ram')+1],'0')
        self.assertEqual(args[args.index('--load-mode')+1],'none')
        self.assertEqual(args[args.index('--reasoning')+1],'on')
        self.assertEqual(args[args.index('--reasoning-budget')+1],'1536')

    def test_lock_digests_and_revisions_are_fixed(self):
        self.assertEqual(len(LOCK['strata']['commit']),40)
        for model in ('flash','rana'):
            for x in LOCK[model]['files']:
                self.assertEqual(len(x['sha256']),64)
                self.assertIn(LOCK[model]['revision'],x['url'])


class GuardTests(unittest.TestCase):
    def test_low_ram_requires_continuous_ten_seconds(self):
        data={'gpus':[],'available_ram_bytes':1*2**30}
        reason,low=limit_reason(data,None,1)
        self.assertIsNone(reason)
        self.assertIsNone(limit_reason(data,low,10.9)[0])
        self.assertIsNotNone(limit_reason(data,low,11)[0])
        data['available_ram_bytes']=3*2**30
        self.assertEqual(limit_reason(data,low,20),(None,None))

    def test_fresh_hbm_sensor_can_stop_below_core_limit(self):
        data={'gpus':[{'temperature_c':55,'memory_temperature_c':80}],'available_ram_bytes':20*2**30}
        self.assertIsNotNone(limit_reason(data,None,0)[0])

    def test_reused_child_pid_is_not_terminated(self):
        process=Mock(pid=101)
        process.poll.return_value=None
        owner=Mock()
        owner.create_time.return_value=1
        child=Mock(pid=102)
        child.create_time.return_value=2
        owner.children.return_value=[child]
        replacement=Mock()
        replacement.create_time.return_value=3
        with patch('reprolib.runtime.psutil.Process',side_effect=lambda pid: owner if pid==101 else replacement):
            register_owned(process)
            stop_owned(process)
        replacement.terminate.assert_not_called()
        process.terminate.assert_called_once()
        OWNED.pop(id(process),None)


class IOTests(unittest.TestCase):
    def test_release_directory_is_normalized_and_notices_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            archive=root/'release.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('release/strata.exe','exe')
                z.writestr('release/BUILD.json','{}')
                z.writestr('release/library.dll','dll')
                z.writestr('LICENSE','notice')
            extract(archive,root/'target')
            self.assertEqual((root/'target/strata.exe').read_text(),'exe')
            self.assertTrue((root/'target/library.dll').exists())
            self.assertTrue((root/'target/LICENSE').exists())

    def test_zip_cannot_write_outside_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z: z.writestr('../escape.txt','bad')
            with self.assertRaises(RuntimeError): extract(archive,root/'target')
            self.assertFalse((root/'escape.txt').exists())

    def test_ranges_are_verified_before_rename(self):
        payload=bytes(range(256))*8
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                first,last=map(int,self.headers['Range'][6:].split('-'))
                part=payload[first:last+1]
                self.send_response(206)
                self.send_header('Content-Range',f'bytes {first}-{last}/{len(payload)}')
                self.send_header('Content-Length',str(len(part)))
                self.end_headers()
                self.wfile.write(part)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
                destination=Path(temp)/'weights.gguf'
                url=f'http://127.0.0.1:{server.server_port}/model'
                download(url,destination,workers=2,published_size=len(payload),
                         published_sha256=hashlib.sha256(payload).hexdigest(),segment_size=256)
                self.assertEqual(destination.read_bytes(),payload)
                wrong=Path(temp)/'wrong.gguf'
                with self.assertRaises(RuntimeError):
                    download(url,wrong,workers=2,published_size=len(payload),published_sha256='0'*64,segment_size=256)
                self.assertFalse(wrong.exists())
                self.assertTrue(wrong.with_name(wrong.name+'.part').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__=='__main__': unittest.main()
