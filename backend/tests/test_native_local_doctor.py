import importlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


def fixture(tmp_path,monkeypatch):
    m=importlib.import_module('ohi_native.doctor')
    from ohi_native.runtime import InstallOptions
    o=InstallOptions(tmp_path,tmp_path/'state',tmp_path/'claw',tmp_path/'node')
    o.state.mkdir();(o.state/'crm.db').write_bytes(b'unchanged database')
    monkeypatch.setattr(m,'validate_install',lambda o:{'source':{'revision':'abc'},'runtime':{'node':'pinned'}})
    monkeypatch.setattr(m,'preflight',lambda o:{'node':'pinned'})
    monkeypatch.setattr(m,'read_secrets',lambda p:dict(human='private',agent='agent',read='read',proposal='proposal'))
    monkeypatch.setattr(m,'check_services',lambda *a,**kw:None)
    calls=[]
    def local(url,**kwargs):
        calls.append((url,kwargs));return {'request_id':'a'*32,'operation':'list_lead_directory','result':{'total':0,'offset':0,'limit':25,'leads':[]}}
    monkeypatch.setattr(m,'local_json',local)
    return m,o,calls


def test_default_diagnosis_is_read_only_without_inference(tmp_path,monkeypatch):
    m,o,calls=fixture(tmp_path,monkeypatch)
    before={p:p.read_bytes() for p in o.state.rglob('*') if p.is_file()}
    result=m.diagnose(o)
    assert result['ok'] and result['reachable'] and result['live_read'] is None
    assert calls==[]
    assert before=={p:p.read_bytes() for p in o.state.rglob('*') if p.is_file()}
    assert 'private' not in json.dumps(result)


def test_explicit_read_is_one_request_with_safe_output(tmp_path,monkeypatch):
    m,o,calls=fixture(tmp_path,monkeypatch);result=m.diagnose(o,True)
    assert result['ok'] and result['live_read']['total']==0
    assert len(calls)==1 and calls[0][0].endswith('/api/chat/directory')
    assert calls[0][1]['token']=='private'
    assert 'leads' not in json.dumps(result) and 'private' not in json.dumps(result)


@pytest.mark.parametrize('payload',[{'error':{'code':'secret'}},{'request_id':'bad','operation':'list_lead_directory','result':{'total':0}}, {'request_id':'a'*32,'operation':'list_lead_directory','result':{'total':True,'offset':0,'limit':25,'leads':[]}}])
def test_malformed_live_receipt_not_certified(tmp_path,monkeypatch,payload):
    m,o,calls=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'local_json',lambda *a,**kw:payload)
    result=m.diagnose(o,True)
    assert not result['ok'] and result['live_read'] is None
    assert 'secret' not in json.dumps(result)


def test_stopped_not_reported_corrupt(tmp_path,monkeypatch):
    m,o,calls=fixture(tmp_path,monkeypatch)
    def stopped(*a,**kw):raise m.InstallError('service_unavailable','safe')
    monkeypatch.setattr(m,'check_services',stopped)
    result=m.diagnose(o,True)
    assert result['installed'] and result['configured'] and not result['reachable']
    assert result['exit_code']==1 and calls==[]


def test_invalid_config_skips_services(tmp_path,monkeypatch):
    m,o,calls=fixture(tmp_path,monkeypatch)
    def bad(*a):raise m.InstallError('installation_changed','safe')
    monkeypatch.setattr(m,'validate_install',bad)
    monkeypatch.setattr(m,'check_services',lambda *a:pytest.fail('invalid state used'))
    result=m.diagnose(o)
    assert result['exit_code']==2 and not result['configured']
