import importlib
import pytest


@pytest.mark.parametrize('value,expected',[(None,False),('0',False),('1',True)])
def test_mode_flag(monkeypatch,value,expected):
    if value is None:monkeypatch.delenv('OHI_NATIVE_ONLY',raising=False)
    else:monkeypatch.setenv('OHI_NATIVE_ONLY',value)
    assert importlib.import_module('app.runtime_mode').native_only() is expected


@pytest.mark.parametrize('value',['true','yes','2',''])
def test_invalid_mode_fails(monkeypatch,value):
    monkeypatch.setenv('OHI_NATIVE_ONLY',value)
    with pytest.raises(RuntimeError):importlib.import_module('app.runtime_mode').native_only()


def test_native_requires_capability_auth(monkeypatch):
    from app.main import startup
    monkeypatch.setenv('OHI_NATIVE_ONLY','1')
    monkeypatch.delenv('OHI_API_TOKEN',raising=False);monkeypatch.delenv('OHI_AGENT_API_TOKEN',raising=False)
    with pytest.raises(RuntimeError,match='capability'):startup()


def test_native_status_and_general_chat_blocked_before_dispatch(client,monkeypatch):
    from app.routers import chat
    monkeypatch.setenv('OHI_NATIVE_ONLY','1');monkeypatch.setenv('OHI_API_TOKEN','h'*32);monkeypatch.setenv('OHI_AGENT_API_TOKEN','a'*32)
    status=client.get('/api/auth/status').json()
    assert status=={'mode':'capabilities','role':None,'workflow_mode':'native'}
    monkeypatch.setattr(chat,'get_driver',lambda:pytest.fail('general model dispatched'))
    headers={'X-API-Token':'h'*32}
    r=client.post('/api/chat',headers=headers,json={'message':'follow up','session_id':'native-test'})
    assert r.status_code==409 and r.json()['error']['code']=='native_only'
    assert client.get('/api/chat/history?session_id=native-test',headers=headers).json()==[]


def test_standard_mode_keeps_general_chat(client,monkeypatch):
    monkeypatch.delenv('OHI_NATIVE_ONLY',raising=False)
    assert client.get('/api/auth/status').json()['workflow_mode']=='standard'
    assert client.post('/api/chat',json={'message':'hello'}).status_code==200
