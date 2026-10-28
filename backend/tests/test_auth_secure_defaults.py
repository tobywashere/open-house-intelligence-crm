"""The API must not silently grant human authority or trust a shared agent key."""
import pytest
import os
import shutil
import subprocess
import sys
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def clean_auth(monkeypatch):
    for name in ('OHI_API_TOKEN', 'OHI_AGENT_API_TOKEN', 'OHI_DEMO_MODE', 'HOST'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AGENT_MODE', 'mock')
    monkeypatch.setenv('INTEGRATIONS_MODE', 'off')


def test_missing_credentials_fail_at_startup(clean_auth):
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_single_shared_credential_is_rejected(clean_auth, monkeypatch):
    monkeypatch.setenv('OHI_API_TOKEN', 'h' * 40)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


@pytest.mark.parametrize('setting,value', [
    ('AGENT_MODE', 'openclaw'), ('INTEGRATIONS_MODE', 'live'),
    ('HOST', '0.0.0.0'), ('OHI_DEMO_MODE', 'yes'),
])
def test_demo_rejects_unsafe_configuration(clean_auth, monkeypatch, setting, value):
    monkeypatch.setenv('OHI_DEMO_MODE', '1')
    monkeypatch.setenv(setting, value)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_legacy_agent_cannot_run_with_human_credentials(clean_auth, monkeypatch):
    monkeypatch.setenv('OHI_API_TOKEN', 'h' * 40)
    monkeypatch.setenv('OHI_AGENT_API_TOKEN', 'a' * 40)
    monkeypatch.setenv('AGENT_MODE', 'openclaw')
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


@pytest.mark.parametrize('peer,host,origin', [
    ('192.0.2.5', 'localhost', None),
    ('127.0.0.1', 'attacker.example', None),
    ('127.0.0.1', 'localhost', 'https://attacker.example'),
])
def test_demo_rejects_nonlocal_and_foreign_browser_requests(
    clean_auth, monkeypatch, peer, host, origin,
):
    monkeypatch.setenv('OHI_DEMO_MODE', '1')
    with TestClient(app, client=(peer, 50000), base_url='http://' + host) as client:
        headers = {'Origin': origin} if origin else {}
        response = client.post('/api/leads', json={'name': 'Blocked', 'source': 'note'}, headers=headers)
        assert response.status_code == 403


def test_explicit_local_demo_can_create_synthetic_lead(clean_auth, monkeypatch):
    monkeypatch.setenv('OHI_DEMO_MODE', '1')
    with TestClient(app, client=('127.0.0.1', 50000), base_url='http://localhost') as client:
        response = client.post('/api/leads', json={'name': 'Synthetic', 'source': 'note'})
        assert response.status_code == 200


def test_removing_credentials_while_running_fails_closed(clean_auth, monkeypatch):
    monkeypatch.setenv('OHI_API_TOKEN', 'h' * 40)
    monkeypatch.setenv('OHI_AGENT_API_TOKEN', 'a' * 40)
    with TestClient(app) as client:
        monkeypatch.delenv('OHI_API_TOKEN')
        monkeypatch.delenv('OHI_AGENT_API_TOKEN')
        assert client.post('/api/pending-changes/1/approve').status_code == 503


@pytest.mark.parametrize('launcher', ['dev.sh', 'serve.sh'])
def test_launchers_reject_missing_keys_before_installing_or_seeding(clean_auth, tmp_path, launcher):
    root = Path(__file__).resolve().parents[2]
    for name in ['scripts/' + launcher, 'scripts/load-env.sh', 'scripts/check_auth.py', 'backend/app/auth_config.py']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    # Only the dependency-free policy check may invoke Python. A regression
    # reaching dependency installation cannot download anything in this test.
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    python = binaries / 'python3'
    import shlex
    python.write_text('#!/bin/sh\n[ "$1" = scripts/check_auth.py ] || exit 90\nexec ' + shlex.quote(sys.executable) + ' -S "$@"\n')
    python.chmod(0o755)
    env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH']}
    result = subprocess.run(['bash', 'scripts/' + launcher], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2, result.stderr
    assert not (tmp_path / '.venv').exists()
    assert not (tmp_path / 'backend/data').exists()


def test_real_crm_read_and_approval_are_denied_to_spoofed_agent(capability_client):
    from tests.conftest import AGENT_TOKEN
    client = capability_client
    assert client.post('/api/leads', json={'name': 'Human', 'source': 'note'}).status_code == 200
    assert client.get('/api/leads', headers={'X-API-Token': AGENT_TOKEN}).status_code == 200
    for headers in ({'X-API-Token': AGENT_TOKEN}, {'X-API-Token': AGENT_TOKEN, 'X-Actor': 'human'}):
        assert client.post('/api/leads', headers=headers, json={'name': 'Forbidden', 'source': 'note'}).status_code == 403
        assert client.post('/api/pending-changes/1/approve', headers=headers).status_code == 403
    assert len(client.get('/api/leads').json()) == 1


def test_legacy_setup_cli_is_retired_before_provisioning(clean_auth, tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / 'scripts/setup_openclaw.py')],
        cwd=tmp_path, env={**os.environ, 'HOME': str(tmp_path)},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 2
    assert 'NATIVE-CREATE-LEAD.md' in result.stderr
    assert not (tmp_path / '.openclaw').exists()
