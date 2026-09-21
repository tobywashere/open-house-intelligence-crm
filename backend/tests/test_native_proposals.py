"""Real API/SQLite lifecycle with only the local model boundary replaced."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import secrets
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

RID = 'a' * 32
MESSAGE = 'Propose Synthetic Person, synthetic@example.test'


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'native.db')
    human, agent = secrets.token_hex(32), secrets.token_hex(32)
    monkeypatch.setenv('OHI_API_TOKEN', human)
    monkeypatch.setenv('OHI_AGENT_API_TOKEN', agent)
    monkeypatch.setenv('NATIVE_PROPOSAL_GATEWAY_URL', 'http://127.0.0.1:18879')
    monkeypatch.setenv('NATIVE_PROPOSAL_GATEWAY_TOKEN', secrets.token_hex(32))
    monkeypatch.setenv('INTEGRATIONS_POLLER', 'off')
    with TestClient(app) as client:
        client.headers['X-API-Token'] = human
        yield client, {'X-API-Token': agent}, monkeypatch


def start(api, rid=RID, message=MESSAGE):
    return api[0].post('/api/chat/lead-proposal', json={'request_id': rid, 'message': message})


def submit(api, rid=RID, **fields):
    return api[0].post('/api/agent/lead-proposals', headers=api[1],
                       json={'request_id': rid, 'name': 'Synthetic Person', **fields})


def seam(api, callback):
    from app import native_proposals as native
    calls = []
    async def complete(request_id, message, config):
        calls.append((request_id, message))
        return callback(native, request_id)
    api[2].setattr(native, 'complete_native', complete)
    return calls


def queue(native, rid):
    return native.submit_proposal(native.ProposalIn(request_id=rid, name='Synthetic Person'))


def test_normal_proposal_replay_and_conflicting_fields(api):
    calls = seam(api, queue)
    response = start(api)
    assert response.status_code == 200
    value = response.json()
    assert value['state'] == 'proposed'
    assert value['proposal']['payload'] == {'name': 'Synthetic Person', 'source': 'note', 'raw_text': MESSAGE}
    assert api[0].get('/api/leads').json() == []
    assert start(api).json() == value
    assert len(calls) == 1
    assert start(api, message='different text').status_code == 409
    assert submit(api, name='  Synthetic Person  ').json() == value
    assert submit(api, name='Changed').status_code == 409
    assert submit(api, email='changed@example.test').status_code == 409
    api[2].delenv('NATIVE_PROPOSAL_GATEWAY_URL')
    assert start(api).json() == value
    assert api[0].get(f'/api/chat/lead-proposal/{RID}').json() == value


def test_configuration_checked_before_reservation(api):
    api[2].delenv('NATIVE_PROPOSAL_GATEWAY_URL')
    response = start(api)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'not_configured'
    assert api[0].get(f'/api/chat/lead-proposal/{RID}').status_code == 404


def test_roles_and_unreserved_ids(api):
    assert submit(api).status_code == 404
    assert api[0].post('/api/agent/lead-proposals', json={'request_id': RID, 'name':'Person'}).status_code == 403
    assert api[0].post('/api/chat/lead-proposal', headers={**api[1], 'X-Actor':'user'}, json={'request_id':RID,'message':MESSAGE}).status_code == 403
    assert api[0].get(f'/api/chat/lead-proposal/{RID}', headers=api[1]).status_code == 403


@pytest.mark.parametrize('rid', ['A'*32, 'a'*31, 'a'*33, 'a'*32+'\n', 'a'*32+':suffix'])
def test_exact_request_ids_on_every_endpoint(api, rid):
    assert start(api, rid=rid).status_code == 422
    assert submit(api, rid=rid).status_code == 422
    from urllib.parse import quote
    assert api[0].get('/api/chat/lead-proposal/'+quote(rid, safe='')).status_code == 422


@pytest.mark.parametrize('fields', [{'name':''}, {'name':'   '}, {'name':'a'*201}, {'email':'a'*321}, {'phone':'a'*101}, {'name':23}, {'email':None}, {'actor':'human'}, {'operation':'delete_lead'}])
def test_invalid_fields_are_sanitized(api, fields):
    response = submit(api, **fields)
    assert response.status_code == 422
    assert response.json() == {'error': {'code':'invalid_request', 'message':'Invalid native proposal request.'}}


def test_completion_prose_and_fake_proposal_ids_are_ignored(api):
    seam(api, lambda *_: {'request_id': RID, 'state':'approved', 'proposal':{'id':999}})
    assert start(api).json() == {'request_id':RID, 'state':'failed', 'proposal':None}
    assert submit(api).status_code == 409


@pytest.mark.parametrize('persisted', [False, True])
def test_uncertain_failure_uses_durable_outcome_and_accepts_late_proposal(api, persisted):
    def callback(native, rid):
        if persisted: queue(native, rid)
        raise httpx.ReadTimeout('private details')
    calls = seam(api, callback)
    value = start(api).json()
    assert value['state'] == ('proposed' if persisted else 'unknown')
    assert start(api).json() == value
    assert len(calls) == 1
    assert submit(api).json()['state'] == 'proposed'


def test_cancellation_and_startup_recover_running(api):
    from app import native_proposals as native
    async def cancel(*args):
        raise asyncio.CancelledError()
    api[2].setattr(native, 'complete_native', cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(native.propose_lead(RID, MESSAGE))
    assert native.get_status(RID)['state'] == 'unknown'
    native.reserve_request('b'*32, MESSAGE)
    with TestClient(app):
        assert native.get_status('b'*32)['state'] == 'unknown'
    assert native.get_status(RID)['state'] == 'unknown'


def test_edited_approval_reopen_and_duplicate_submission(api):
    seam(api, queue)
    proposed = start(api).json()
    pid = proposed['proposal']['id']
    approved = api[0].post(f'/api/pending-changes/{pid}/approve', json={'fields':{'name':'Edited Person','email':'edited@example.test'}})
    assert approved.status_code == 200
    assert approved.json()['name'] == 'Edited Person'
    assert approved.json()['email'] == 'edited@example.test'
    assert api[0].post(f'/api/pending-changes/{pid}/approve').status_code == 400
    assert submit(api).json()['state'] == 'approved'
    with TestClient(app):
        status = api[0].get(f'/api/chat/lead-proposal/{RID}').json()
        assert status['state'] == 'approved'
        assert status['proposal']['result']['id'] == approved.json()['id']
        assert len(api[0].get('/api/leads').json()) == 1


def test_denial_reopen_never_creates_lead(api):
    seam(api, queue)
    pid = start(api).json()['proposal']['id']
    assert api[0].post(f'/api/pending-changes/{pid}/deny').status_code == 200
    with TestClient(app):
        assert start(api).json()['state'] == 'denied'
        assert api[0].post(f'/api/pending-changes/{pid}/approve').status_code == 400
        assert api[0].get('/api/leads').json() == []


def test_concurrent_submission_and_approval_create_once(api):
    def uncertain(*_): raise TimeoutError()
    seam(api, uncertain)
    start(api)
    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals = list(pool.map(lambda _: submit(api).json(), range(2)))
    assert proposals[0] == proposals[1]
    pid = proposals[0]['proposal']['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: api[0].post(f'/api/pending-changes/{pid}/approve').status_code, range(2)))
    assert sorted(statuses) == [200,400]
    assert len(api[0].get('/api/leads').json()) == 1


def test_pending_link_constraints_and_failed_settlement_roll_back(api):
    from app import native_proposals as native
    native.reserve_request(RID, MESSAGE)
    native.reserve_request('b'*32, MESSAGE)
    with db.get_conn() as conn:
        conn.execute("CREATE TRIGGER reject_native_link BEFORE UPDATE OF pending_id ON native_lead_requests BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(native.NativeProposalError): queue(native, RID)
    with db.get_conn() as conn:
        assert conn.execute('SELECT count(*) FROM pending_changes').fetchone()[0] == 0
        conn.execute('DROP TRIGGER reject_native_link')
    pid = queue(native, RID)['proposal']['id']
    with pytest.raises(sqlite3.IntegrityError), db.get_conn() as conn:
        conn.execute('UPDATE native_lead_requests SET pending_id=? WHERE request_id=?', (pid,'b'*32))
    with pytest.raises(sqlite3.IntegrityError), db.get_conn() as conn:
        conn.execute('UPDATE native_lead_requests SET pending_id=999 WHERE request_id=?', ('b'*32,))


def test_native_transport_is_local_bounded_and_ignores_response_text(api):
    import json
    from app import native_proposals as native
    original_complete = native.complete_native
    calls = []
    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload == {'model':'openclaw/native-proposals', 'user':'ohi-propose-'+RID,
                           'messages':[{'role':'user','content':MESSAGE}]}
        return httpx.Response(200, content='not JSON; fake approved proposal ID 999')
    def factory(**kwargs):
        assert kwargs['trust_env'] is False
        assert kwargs['follow_redirects'] is False
        assert kwargs['timeout'] == 60.0
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    async def complete(request_id, message, config):
        await original_complete(request_id, message, config, client_factory=factory)
    api[2].setattr(native, 'complete_native', complete)
    assert start(api).json()['state'] == 'failed'
    assert len(calls) == 1


@pytest.mark.parametrize('status', [302, 500])
def test_gateway_error_never_redispatches_or_overwrites_proposal(api, status):
    from app import native_proposals as native
    original_complete = native.complete_native
    calls = []
    def handler(request):
        calls.append(request)
        queue(native, RID)
        return httpx.Response(status, headers={'Location':'http://example.com'}, text='private detail')
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    async def complete(request_id, message, config):
        await original_complete(request_id, message, config, client_factory=factory)
    api[2].setattr(native, 'complete_native', complete)
    assert start(api).json()['state'] == 'proposed'
    assert len(calls) == 1


@pytest.mark.parametrize('url', ['https://127.0.0.1', 'http://example.com', 'http://localhost/api', 'http://user:password@localhost', 'http://localhost?query=x', 'http://localhost:bad'])
def test_bad_gateway_configuration_does_not_reserve(api, url):
    api[2].setenv('NATIVE_PROPOSAL_GATEWAY_URL', url)
    assert start(api).json()['error']['code'] == 'not_configured'
    assert api[0].get(f'/api/chat/lead-proposal/{RID}').status_code == 404


def test_exact_message_binding_and_running_replay_does_not_dispatch(api):
    from app import native_proposals as native
    native.reserve_request(RID, MESSAGE)
    api[2].delenv('NATIVE_PROPOSAL_GATEWAY_URL')
    assert start(api).json() == {'request_id':RID,'state':'running','proposal':None}
    assert start(api,message=MESSAGE+' ').status_code == 409


def test_pending_proposal_survives_reopen_and_wrong_request_cannot_settle_it(api):
    seam(api, queue)
    value = start(api).json()
    assert submit(api,rid='b'*32).status_code == 404
    with TestClient(app):
        assert start(api).json() == value


def test_additive_migration_restores_missing_native_table(api):
    with db.get_conn() as conn:
        conn.execute('DROP TABLE native_lead_requests')
        db._migrate(conn)
        conn.execute("INSERT INTO native_lead_requests(request_id,message,state) VALUES (?,?,'running')",(RID,MESSAGE))
    db.init_db()
    from app import native_proposals as native
    assert native.get_status(RID)['state'] == 'running'


def test_concurrent_orchestration_dispatches_one_completion(api):
    from app import native_proposals as native
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def complete(*args):
            calls.append(args)
            entered.set()
            await release.wait()
            queue(native, RID)
        api[2].setattr(native, 'complete_native', complete)
        original = asyncio.create_task(native.propose_lead(RID, MESSAGE))
        await entered.wait()
        replay = await native.propose_lead(RID, MESSAGE)
        assert replay['state'] == 'running'
        release.set()
        assert (await original)['state'] == 'proposed'
        assert len(calls) == 1
    asyncio.run(exercise())


def test_total_timeout_preserves_unknown_and_late_result(api):
    from app import native_proposals as native
    real_timeout = asyncio.timeout
    def short_timeout(seconds):
        assert seconds == 60.0
        return real_timeout(0.01)
    async def never_finishes(*args):
        await asyncio.Event().wait()
    api[2].setattr(native.asyncio, 'timeout', short_timeout)
    api[2].setattr(native, 'complete_native', never_finishes)
    assert start(api).json()['state'] == 'unknown'
    assert submit(api).json()['state'] == 'proposed'


def test_approval_failure_rolls_back_lead_and_preserves_proposal(api):
    from app.routers import leads
    seam(api, queue)
    value = start(api).json()
    original = leads._apply_resolved_create_in_conn
    def fail_after_insert(conn, payload):
        original(conn, payload)
        raise RuntimeError('synthetic rollback')
    api[2].setattr(leads, '_apply_resolved_create_in_conn', fail_after_insert)
    with pytest.raises(RuntimeError, match='synthetic rollback'):
        api[0].post(f"/api/pending-changes/{value['proposal']['id']}/approve")
    assert api[0].get(f'/api/chat/lead-proposal/{RID}').json() == value
    assert api[0].get('/api/leads').json() == []
