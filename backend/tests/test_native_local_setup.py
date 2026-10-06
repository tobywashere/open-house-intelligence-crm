"""Setup runs real DB initialization; only dependency and gateway executables are substituted."""
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))


def setup_module_code():
    assert (ROOT/'scripts/ohi_native/setup.py').exists(), 'native setup implementation missing'
    return importlib.import_module('ohi_native.setup')


def fixture_options(tmp_path,monkeypatch):
    m=setup_module_code()
    from ohi_native import state
    monkeypatch.setattr(state,'environment_identity',lambda options:{'fixture':'simulated dependency installation'})
    from ohi_native.runtime import InstallOptions
    root=tmp_path/'repo';(root/'backend').mkdir(parents=True)
    shutil.copytree(ROOT/'backend/app',root/'backend/app',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT/'backend/schema.sql',root/'backend/schema.sql')
    (root/'backend/requirements-native.lock').write_text('test-lock')
    (root/'dashboard').mkdir();(root/'dashboard/package-lock.json').write_text('{}')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
    o=InstallOptions(root,tmp_path/'state',tmp_path/'bin/openclaw',tmp_path/'bin/node')
    monkeypatch.setattr(m,'preflight',lambda options:{'node':'24.15.0'})
    def build(options,log):
        (root/'.venv-native/bin').mkdir(parents=True,exist_ok=True)
        (root/'.venv-native/bin/python').symlink_to(sys.executable)
        (root/'dashboard/dist/assets').mkdir(parents=True)
        (root/'dashboard/dist/index.html').write_text('<html/>')
        (root/'dashboard/dist/assets/app.js').write_text('const api="/api";')
    monkeypatch.setattr(m,'prepare_dependencies',build)
    seen=[]
    def config_check(options,private_state,log):
        for p in private_state.glob('*/home/.openclaw-*/openclaw.json'):
            data=json.loads(p.read_text());seen.append(data)
        assert (private_state/'human.key').read_text().strip() not in json.dumps(seen)
    monkeypatch.setattr(m,'validate_configs',config_check)
    return m,o,seen


def test_empty_shared_database_and_repeat_setup_preserves_it(tmp_path,monkeypatch):
    m,o,seen=fixture_options(tmp_path,monkeypatch)
    result=m.setup_install(o)
    assert result['created'] is True
    with sqlite3.connect(o.state/'crm.db') as c:
        assert c.execute('select count(*) from leads').fetchone()[0]==0
        assert c.execute('select count(*) from pending_changes').fetchone()[0]==0
        c.execute("insert into leads(name,status) values ('Preserved Person','new')")
    keys={p.name:p.read_bytes() for p in o.state.glob('*.key')}
    configs={p.name:p.read_bytes() for p in o.state.glob('*/home/.openclaw-*/openclaw.json')}
    monkeypatch.setattr(m,'prepare_dependencies',lambda *args:pytest.fail('repeat setup rebuilt dependencies'))
    assert m.setup_install(o)['created'] is False
    assert keys=={p.name:p.read_bytes() for p in o.state.glob('*.key')}
    with sqlite3.connect(o.state/'crm.db') as c:assert c.execute('select name from leads').fetchall()==[('Preserved Person',)]
    urls=[d['plugins']['entries'][p]['config']['crmApiUrl'] for d in seen for p in ('openhouse-read','openhouse-proposals') if p in d['plugins']['entries']]
    assert set(urls)=={'http://127.0.0.1:18080/api'}
    assert (o.state/'crm.db').stat().st_mode&0o777==0o600
    assert (o.state/'manifest.json').stat().st_mode&0o777==0o600


@pytest.mark.parametrize('failure',['dependencies','gateway','database'])
def test_failure_never_publishes_completed_state(tmp_path,monkeypatch,failure):
    m,o,_=fixture_options(tmp_path,monkeypatch)
    def fail(*args):raise m.InstallError('test_failure','failure')
    monkeypatch.setattr(m,{'dependencies':'prepare_dependencies','gateway':'validate_configs','database':'initialize_database'}[failure],fail)
    with pytest.raises(m.InstallError):m.setup_install(o)
    assert not (o.state/'manifest.json').exists()
    assert list(tmp_path.glob('.state.incomplete-*'))


def test_setup_refuses_existing_unrelated_directory(tmp_path,monkeypatch):
    m,o,_=fixture_options(tmp_path,monkeypatch);o.state.mkdir();(o.state/'precious').write_text('unchanged')
    with pytest.raises(m.InstallError):m.setup_install(o)
    assert (o.state/'precious').read_text()=='unchanged'


def test_clean_build_inputs_exclude_dotenv_and_untracked_files(tmp_path):
    m=setup_module_code();root=tmp_path/'repo';(root/'dashboard/src').mkdir(parents=True)
    (root/'dashboard/src/main.ts').write_text('app');(root/'dashboard/.env').write_text('VITE_API_URL=https://evil.invalid')
    (root/'dashboard/.env.production').write_text('VITE_API_URL=https://evil.invalid')
    (root/'dashboard/untracked.ts').write_text('untrusted')
    subprocess.run(['git','init','-q',str(root)],check=True)
    # Even tracked dotenv must not enter a native build.
    subprocess.run(['git','-C',str(root),'add','dashboard/src','dashboard/.env.production'],check=True)
    target=tmp_path/'build';m.copy_dashboard(root,target)
    assert (target/'src/main.ts').read_text()=='app'
    assert not list(target.glob('.env*'))
    assert not (target/'untracked.ts').exists()


def test_cli_invalid_ports_never_create_state(tmp_path):
    assert (ROOT/'scripts/native_local.py').exists(), 'native CLI implementation missing'
    state=tmp_path/'state'
    r=subprocess.run([sys.executable,str(ROOT/'scripts/native_local.py'),'setup','--state',str(state),'--port','80'],capture_output=True,text=True,timeout=5)
    assert r.returncode==2
    assert not state.exists()


def test_dependency_build_environment_isolated(tmp_path,monkeypatch):
    m,o,_=fixture_options(tmp_path,monkeypatch)
    monkeypatch.setenv('VITE_API_URL','https://ambient.invalid')
    monkeypatch.setenv('OHI_API_TOKEN','ambient-human')
    monkeypatch.setenv('HTTPS_PROXY','http://proxy.invalid')
    calls=[]
    def fake(command,**kwargs):
        calls.append((command,kwargs['env']))
        if command[-1]=='build':
            out=kwargs['cwd']/'dist';(out/'assets').mkdir(parents=True)
            (out/'index.html').write_text('ok');(out/'assets/app.js').write_text('fetch("/api")')
    (o.root/'.venv-native/bin').mkdir(parents=True)
    (o.root/'.venv-native/bin/python').symlink_to(sys.executable)
    monkeypatch.setattr(m,'checked',fake)
    # Call the actual function instead of the fixture replacement.
    import importlib
    actual=importlib.reload(m).prepare_dependencies
    monkeypatch.setattr(m,'checked',fake)
    actual(o,tmp_path/'log')
    assert all('VITE_API_URL' not in env and 'OHI_API_TOKEN' not in env for _,env in calls)
    assert 'HTTPS_PROXY' not in calls[-1][1]
    assert '/api' in (o.root/'dashboard/dist/assets/app.js').read_text()


def test_existing_install_rejects_equals_port_override(tmp_path):
    state=tmp_path/'state';state.mkdir(mode=0o700)
    (state/'manifest.json').write_text(json.dumps({'schema_version':1,'complete':True,'openclaw':'/bin/x','node':'/bin/y','ports':[18080,18880,18881]}))
    (state/'manifest.json').chmod(0o600)
    r=subprocess.run([sys.executable,str(ROOT/'scripts/native_local.py'),'setup','--state',str(state),'--port=18081'],capture_output=True,text=True)
    assert 'immutable_options' in r.stderr


def test_preparation_timeout_reaps_descendants(tmp_path):
    m=setup_module_code();pidfile=tmp_path/'descendant'
    script='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"]);open(sys.argv[1],"w").write(str(p.pid));time.sleep(20)'
    try:
        with pytest.raises(m.InstallError):
            m.checked([sys.executable,'-c',script,pidfile],cwd=tmp_path,env={'PATH':'/usr/bin:/bin'},log=tmp_path/'log',timeout=.5)
        status=subprocess.run(['ps','-o','stat=','-p',pidfile.read_text()],capture_output=True,text=True).stdout.strip()
        assert not status or status.startswith('Z')
    finally:
        if pidfile.exists():
            import signal
            try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
