"""Real loopback relay and CRM API, simulated model boundary only."""
from concurrent.futures import ThreadPoolExecutor
import importlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient
from app import db,native_proposals
from app.main import app

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
RID='a'*32


def module():return importlib.import_module('native_first_run_relay')


def post(port,body,token):
    request=urllib.request.Request(f'http://127.0.0.1:{port}/api/agent/lead-proposals',data=json.dumps(body).encode(),headers={'X-API-Token':token,'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:return response.status
    except urllib.error.HTTPError as error:return error.code


def wait_capture(control):
    deadline=time.monotonic()+3
    while not (control/'captured.json').exists() and time.monotonic()<deadline:time.sleep(.02)
    assert (control/'captured.json').exists()


def release(control):
    (control/'release').touch(mode=0o600)


@pytest.mark.parametrize('target',['https://127.0.0.1:1234/api/agent/lead-proposals','http://example.com/api/agent/lead-proposals','http://127.0.0.1:1234/wrong'])
def test_relay_refuses_nonfixed_target(tmp_path,target):
    m=module();key=tmp_path/'key';key.write_text('a'*64);key.chmod(0o600)
    with pytest.raises(m.InstallError):m.make_server(target,key,tmp_path/'control',RID,0)


def test_late_submission_is_rejected_by_real_api_after_close(tmp_path,monkeypatch):
    m=module();human=secrets.token_hex(32);agent=secrets.token_hex(32)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'crm.db')
    for key,value in dict(OHI_API_TOKEN=human,OHI_AGENT_API_TOKEN=agent,NATIVE_PROPOSAL_GATEWAY_URL='http://127.0.0.1:1',NATIVE_PROPOSAL_GATEWAY_TOKEN='private',INTEGRATIONS_POLLER='off').items():monkeypatch.setenv(key,value)
    calls=[]
    async def uncertain(*args):calls.append(args[0]);raise TimeoutError()
    monkeypatch.setattr(native_proposals,'complete_native',uncertain)
    with TestClient(app) as client:
        client.headers['X-API-Token']=human
        assert client.post('/api/chat/lead-proposal',json={'request_id':RID,'message':'Propose Synthetic'}).json()['state']=='unknown'
        forwarded=[]
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']));forwarded.append(self.path)
                response=client.post(self.path,content=body,headers={'Content-Type':'application/json','X-API-Token':self.headers['X-API-Token']})
                self.send_response(response.status_code);self.end_headers();self.wfile.write(response.content)
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
        key=tmp_path/'agent.key';key.write_text(agent);key.chmod(0o600);control=tmp_path/'control'
        relay=m.make_server(f'http://127.0.0.1:{upstream.server_port}/api/agent/lead-proposals',key,control,RID,0)
        runner=threading.Thread(target=relay.serve_forever,daemon=True);runner.start()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending=pool.submit(post,relay.server_port,{'request_id':RID,'name':'Synthetic'},agent)
                wait_capture(control)
                assert post(relay.server_port,{'request_id':RID,'name':'Synthetic'},agent)==409
                assert client.post(f'/api/chat/lead-proposal/{RID}/close').json()['state']=='failed'
                native_proposals.recover_running_requests()
                release(control);assert pending.result(timeout=5)==409
            assert forwarded==['/api/agent/lead-proposals'] and calls==[RID]
            assert client.get('/api/leads').json()==[]
            with db.get_conn() as conn:assert conn.execute('select count(*) from pending_changes').fetchone()[0]==0
            output=(control/'captured.json').read_text()+(control/'result.json').read_text()
            assert agent not in output and human not in output and 'Synthetic' not in output
        finally:
            relay.cancel.set();relay.shutdown();relay.server_close();runner.join()
            upstream.shutdown();upstream.server_close();thread.join()


def test_wrong_id_key_oversize_and_hold_expiry(tmp_path):
    m=module();key=tmp_path/'key';key.write_text('b'*64);key.chmod(0o600)
    control=tmp_path/'control';server=m.make_server('http://127.0.0.1:9/api/agent/lead-proposals',key,control,RID,0,hold_seconds=.1)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert post(server.server_port,{'request_id':'c'*32,'name':'Synthetic'},'b'*64)==400
        assert post(server.server_port,{'request_id':RID,'name':'Synthetic'},'wrong')==401
        assert post(server.server_port,{'request_id':RID,'name':'x'*10000},'b'*64)==413
        assert not (control/'captured.json').exists()
        assert post(server.server_port,{'request_id':RID,'name':'Synthetic'},'b'*64)==504
        assert json.loads((control/'result.json').read_text())['forwarded'] is False
    finally:server.cancel.set();server.shutdown();server.server_close();thread.join()


def test_relay_never_follows_upstream_redirect(tmp_path):
    m=module();seen=[]
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            seen.append(self.path);self.send_response(307);self.send_header('Location','/secret-target');self.end_headers()
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    key=tmp_path/'key';key.write_text('b'*64);key.chmod(0o600);control=tmp_path/'control'
    relay=m.make_server(f'http://127.0.0.1:{upstream.server_port}/api/agent/lead-proposals',key,control,RID,0)
    worker=threading.Thread(target=relay.serve_forever,daemon=True);worker.start()
    try:
        with ThreadPoolExecutor() as pool:
            pending=pool.submit(post,relay.server_port,{'request_id':RID,'name':'Synthetic'},'b'*64)
            wait_capture(control);release(control);assert pending.result(timeout=5)==307
        assert seen==['/api/agent/lead-proposals']
    finally:
        relay.cancel.set();relay.shutdown();relay.server_close();worker.join()
        upstream.shutdown();upstream.server_close();thread.join()
