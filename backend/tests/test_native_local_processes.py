"""Simulated process lifecycle; these checks do not exercise model inference."""
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))


def module():
    return importlib.import_module('ohi_native.processes')


def test_occupied_listener_is_not_adopted_or_closed():
    m=module()
    with socket.socket() as owner:
        owner.bind(('127.0.0.1',0));owner.listen()
        with pytest.raises(OSError):m.reserve_listener(owner.getsockname()[1])
        assert owner.fileno()>=0


def test_owned_process_group_cleanup_includes_descendants(tmp_path):
    m=module();pidfile=tmp_path/'child'
    script='import subprocess,sys,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=subprocess.Popen([sys.executable,"-c","import time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(60)'
    p=subprocess.Popen([sys.executable,'-c',script,str(pidfile)],start_new_session=True)
    try:
        deadline=time.monotonic()+3
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert pidfile.exists()
        start=time.monotonic();m.stop_children([p],grace=.1)
        assert time.monotonic()-start<3
        assert p.poll() is not None
        # A killed orphan can briefly remain a zombie; it must not be runnable.
        status=subprocess.run(['ps','-o','stat=','-p',pidfile.read_text()],capture_output=True,text=True).stdout.strip()
        assert not status or status.startswith('Z')
    finally:
        try:os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        p.wait()


def options(tmp_path):
    from ohi_native.runtime import InstallOptions
    return InstallOptions(tmp_path,tmp_path/'state',tmp_path/'openclaw',Path(sys.executable))


@pytest.mark.parametrize('payload',[{'ok':False,'agents':[{'agentId':'native-read'}]}, {'ok':True,'agents':[{'agentId':'wrong'}]}])
def test_gateway_probe_rejects_wrong_identity(tmp_path,monkeypatch,payload):
    m=module();o=options(tmp_path)
    monkeypatch.setattr(m,'command_json',lambda *a,**kw:payload)
    with pytest.raises(m.InstallError):m.gateway_health(o,'read',{'read':{},'proposal':{}})


def test_gateway_probe_has_no_secret_in_arguments(tmp_path,monkeypatch):
    m=module();o=options(tmp_path);seen=[]
    def run(command,**kwargs):
        seen.append((command,kwargs));return {'ok':True,'agents':[{'agentId':'native-read'}]}
    monkeypatch.setattr(m,'command_json',run)
    assert m.gateway_health(o,'read',{'read':{'HOME':'private','OHI_AGENT_API_TOKEN':'hidden'}})
    assert 'hidden' not in repr(seen[0][0])
    assert 'call' in seen[0][0] and 'health' in seen[0][0]


def test_late_child_exit_fails_supervision():
    m=module();p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.05)'],start_new_session=True)
    try:
        with pytest.raises(m.InstallError,match='child'):m.supervise([p],lambda:False,interval=.02)
    finally:m.stop_children([p],grace=.1)


def test_startup_timeout_stops_waiting(monkeypatch):
    m=module();p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],start_new_session=True)
    def unready():raise m.InstallError('stopped','not ready')
    try:
        with pytest.raises(m.InstallError,match='ready'):m.wait_ready([p],unready,lambda:False,timeout=.1)
    finally:m.stop_children([p],grace=.1)


def test_stale_install_fails_before_spawn(tmp_path,monkeypatch):
    m=module();o=options(tmp_path)
    def stale(options):raise m.InstallError('installation_changed','restore matching files')
    monkeypatch.setattr(m,'validate_install',stale)
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**kw:pytest.fail('spawned from invalid state'))
    with pytest.raises(m.InstallError):m.run_install(o)


def test_runtime_mismatch_fails_before_spawn(tmp_path,monkeypatch):
    m=module();o=options(tmp_path)
    monkeypatch.setattr(m,'validate_install',lambda o:{'runtime':{'node':'pinned'}})
    monkeypatch.setattr(m,'preflight',lambda o:{'node':'changed'})
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**kw:pytest.fail('spawned from mismatched runtime'))
    with pytest.raises(m.InstallError,match='runtime'):m.run_install(o)


@pytest.mark.parametrize('late_exit',[False,True])
def test_foreground_start_signal_and_late_failure_from_other_cwd(tmp_path,late_exit):
    import selectors
    m=module();root=tmp_path/'repo';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    for name in ('logs','backend-home','read/workspace','read/home','proposal/workspace','proposal/home'):(state/name).mkdir(parents=True,exist_ok=True,mode=0o700)
    (root/'.venv-native/bin').mkdir(parents=True)
    ports=[]
    with m.reserve_listener(0) as a,m.reserve_listener(0) as b,m.reserve_listener(0) as c:
        ports=[x.getsockname()[1] for x in (a,b,c)]
    program='''import http.server,json,os,pathlib,socket,sys,urllib.request
args=sys.argv[1:]
root=pathlib.Path(__file__).resolve().parents[2] if '-m' in args else pathlib.Path(__file__).parent
if 'call' in args:
 port=int(args[args.index('--port')+1]);profile=args[args.index('--profile')+1]
 r=urllib.request.Request('http://127.0.0.1:'+str(port)+'/health',headers={'Authorization':'Bearer '+profile})
 print(urllib.request.urlopen(r,timeout=1).read().decode());sys.exit()
backend='-m' in args
profile='backend' if backend else args[args.index('--profile')+1]
(root/(profile+'.pid')).write_text(str(os.getpid()))
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  if backend:
   good=self.headers.get('X-API-Token')=='human'
   value={'mode':'capabilities','role':'human','workflow_mode':'native'}
  else:
   good=self.headers.get('Authorization')=='Bearer '+profile
   value={'ok':True,'agents':[{'agentId':'native-read' if profile.endswith('read') else 'native-proposals'}]}
  self.send_response(200 if good else 401);self.end_headers();self.wfile.write(json.dumps(value).encode())
server=http.server.HTTPServer(('127.0.0.1',0),Handler,bind_and_activate=False)
if backend:server.socket=socket.socket(fileno=int(args[args.index('--fd')+1]))
else:
 server.server_address=('127.0.0.1',int(args[args.index('--port')+1]));server.server_bind();server.server_activate()
server.timeout=.05
while not (root/'crash').exists():server.handle_request()
'''
    for p in (root/'.venv-native/bin/python',root/'openclaw'):
        p.write_text('#!'+sys.executable+'\n'+program);p.chmod(0o700)
    worker=tmp_path/'worker.py'
    worker.write_text(f'''import sys
from pathlib import Path
sys.path.insert(0,{str(ROOT/'scripts')!r})
from ohi_native import processes as m
from ohi_native.runtime import InstallOptions
m.validate_install=lambda o:{{'runtime':{{}},'source':{{'revision':'test'}}}}
m.preflight=lambda o:{{}}
m.read_secrets=lambda p:dict(human='human',agent='agent',read='read',proposal='proposal')
o=InstallOptions(Path({str(root)!r}),Path({str(state)!r}),Path({str(root/'openclaw')!r}),Path({sys.executable!r}),{tuple(ports)!r})
try:sys.exit(m.run_install(o))
except m.InstallError as e:print(e.code,flush=True);sys.exit(2)
''')
    p=subprocess.Popen([sys.executable,str(worker)],cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout,selectors.EVENT_READ)
            assert selector.select(10),'launcher never became ready'
            line=p.stdout.readline();assert json.loads(line)['ready'] is True,line
        busy=subprocess.run([sys.executable,str(worker)],cwd=tmp_path,capture_output=True,text=True,timeout=3)
        assert busy.returncode==2 and 'busy' in busy.stdout
        if late_exit:(root/'crash').touch()
        else:p.send_signal(signal.SIGTERM)
        out,err=p.communicate(timeout=10)
        if err: print(err)
        assert p.returncode==(2 if late_exit else 0),(out,err)
        for port in ports:
            # An active HTTP listener must be gone; TIME_WAIT can delay rebinding.
            with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0
    finally:
        if p.poll() is None:p.terminate();p.wait(timeout=10)
