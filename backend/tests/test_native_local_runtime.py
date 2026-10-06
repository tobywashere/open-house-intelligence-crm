"""Native install boundaries; fake only external runtime discovery and HTTP transport."""
import importlib
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))


def runtime():
    assert (ROOT/'scripts/ohi_native/runtime.py').exists(), 'native runtime implementation missing'
    return importlib.import_module('ohi_native.runtime')


def options(tmp_path):
    r = runtime()
    return r.InstallOptions(tmp_path/'repo', tmp_path/'state', tmp_path/'bin/openclaw', tmp_path/'bin/node')


def test_separated_config_and_environment(tmp_path, monkeypatch):
    r = runtime(); o = options(tmp_path)
    for key in ('OHI_API_TOKEN','OPENAI_API_KEY','HTTP_PROXY','OPENCLAW_CONFIG_PATH','VITE_API_URL'):
        monkeypatch.setenv(key, 'ambient-do-not-inherit')
    secrets = {name: name + '-test-secret' for name in ('human','agent','read','proposal')}
    env = r.child_environments(o, secrets)
    assert env['backend']['OHI_API_TOKEN'] == 'human-test-secret'
    assert env['backend']['DB_PATH'] == str(o.state/'crm.db')
    assert env['backend']['OHI_NATIVE_ONLY'] == '1'
    for kind,tool,plugin,agent in [('read','openhouse_crm','openhouse-read','native-read'),
                                  ('proposal','openhouse_propose_lead','openhouse-proposals','native-proposals')]:
        assert 'OHI_API_TOKEN' not in env[kind]
        assert env[kind]['OHI_AGENT_API_TOKEN'] == 'agent-test-secret'
        assert 'ambient-do-not-inherit' not in json.dumps(env)
        c = r.gateway_config(o,kind,secrets[kind])
        assert c['tools']['allow'] == [tool]
        assert c['agents']['entries'][agent]['tools']['allow'] == [tool]
        assert c['plugins']['entries'][plugin]['config']['crmApiUrl'] == 'http://127.0.0.1:18080/api'
        assert c['agents']['defaults']['model']['fallbacks'] == []
        assert 'human-test-secret' not in json.dumps(c)
    assert env['read']['HOME'] != env['proposal']['HOME']
    assert env['backend']['NATIVE_READ_GATEWAY_URL'] == 'http://127.0.0.1:18880'
    assert env['backend']['NATIVE_PROPOSAL_GATEWAY_URL'] == 'http://127.0.0.1:18881'


@pytest.mark.parametrize('ports',[(80,18880,18881),(18080,18080,18881),(18080,18880,70000)])
def test_invalid_ports_refused(tmp_path,ports):
    r=runtime()
    with pytest.raises(ValueError):
        r.InstallOptions(tmp_path/'repo',tmp_path/'state',tmp_path/'bin/openclaw',tmp_path/'bin/node',ports)


def metadata(path):
    if path.endswith('/version'): return {'version':'0.32.15'}
    return {'models':[{'name':'qwen3.5:9b','digest':'6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'}]}


def prepared_preflight(tmp_path,monkeypatch):
    r=runtime();o=options(tmp_path)
    monkeypatch.setattr(r,'host_identity',lambda: 'ubuntu-24.04-x86_64-python3.12')
    def version(binary,env):
        return 'v24.15.0' if binary.name=='node' else 'OpenClaw 2026.8.1-beta.3 (5831b80)'
    monkeypatch.setattr(r,'executable_version',version)
    monkeypatch.setattr(r,'local_json',metadata)
    return r,o


def test_preflight_requires_exact_runtime_and_model(tmp_path,monkeypatch):
    r,o=prepared_preflight(tmp_path,monkeypatch)
    result=r.preflight(o)
    assert result['node']=='24.15.0'
    assert result['ollama']=='0.32.15'
    assert result['model_digest']=='6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'


@pytest.mark.parametrize('field', ['node','openclaw','ollama','digest','missing_model','host'])
def test_preflight_rejects_mismatch(tmp_path,monkeypatch,field):
    r,o=prepared_preflight(tmp_path,monkeypatch)
    if field=='host':monkeypatch.setattr(r,'host_identity',lambda:'macos')
    elif field in ('node','openclaw'):
        original=r.executable_version
        monkeypatch.setattr(r,'executable_version',lambda binary,env: 'untrusted-other-version' if binary.name==field else original(binary,env))
    else:
        def changed(path):
            data=metadata(path)
            if field=='ollama' and path.endswith('/version'):data['version']='0.0.1'
            if path.endswith('/tags'):
                if field=='digest':data['models'][0]['digest']='wrong'
                if field=='missing_model':data['models']=[]
            return data
        monkeypatch.setattr(r,'local_json',changed)
    with pytest.raises(r.InstallError):r.preflight(o)


@pytest.mark.parametrize('body',[b'not json',b'[]',b'x'*1_048_577],ids=['invalid-json','wrong-type','oversized'])
def test_local_json_rejects_bad_or_large_response(monkeypatch,body):
    r=runtime()
    class Opener:
        def open(self,req,timeout):return io.BytesIO(body)
    monkeypatch.setattr(r.urllib.request,'build_opener',lambda *handlers:Opener())
    with pytest.raises(r.InstallError):r.local_json('http://127.0.0.1:11434/api/version')


def test_local_json_ignores_proxies_and_refuses_redirect(monkeypatch):
    r=runtime();seen=[]
    original=r.urllib.request.build_opener
    def factory(*handlers):
        seen.extend(handlers)
        return original(*handlers)
    monkeypatch.setattr(r.urllib.request,'build_opener',factory)
    monkeypatch.setenv('HTTP_PROXY','http://elsewhere.invalid')
    # The real redirect handler must refuse even a same-host redirect.
    opener=r.local_opener()
    assert any(isinstance(h,r.urllib.request.ProxyHandler) and h.proxies=={} for h in seen)
    handler=next(h for h in opener.handlers if isinstance(h,r.urllib.request.HTTPRedirectHandler))
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(r.urllib.request.Request('http://127.0.0.1'),None,302,'redirect',{},'http://other.invalid')


def test_transport_failure_is_redacted(monkeypatch):
    r=runtime()
    class Opener:
        def open(self,*args,**kwargs):raise TimeoutError('secret-response')
    monkeypatch.setattr(r,'local_opener',lambda:Opener())
    with pytest.raises(r.InstallError) as caught:r.local_json('http://127.0.0.1:11434/api/version')
    assert 'secret-response' not in str(caught.value)
