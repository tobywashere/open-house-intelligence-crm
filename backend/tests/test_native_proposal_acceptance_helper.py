"""Acceptance helper safety behavior; no real OpenClaw or user profile involved."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('proposal_acceptance', ROOT / 'scripts/native_proposal_acceptance.py')


def helper():
    assert SPEC.origin and Path(SPEC.origin).exists(), 'native proposal acceptance helper is missing'
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_private_profile_refuses_overwrite_and_splits_credentials(tmp_path, monkeypatch):
    h = helper()
    monkeypatch.setenv('OHI_API_TOKEN', 'ambient-human-do-not-inherit')
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-provider-do-not-inherit')
    state = tmp_path / 'private-profile'
    h.prepare(state, 'ohi-test-proposals', 18082, 18882)
    with pytest.raises(FileExistsError):
        h.prepare(state, 'ohi-test-proposals', 18082, 18882)
    backend, gateway = h.environments(state)
    assert backend['OHI_API_TOKEN'] != backend['OHI_AGENT_API_TOKEN']
    assert gateway['OHI_AGENT_API_TOKEN'] == backend['OHI_AGENT_API_TOKEN']
    assert 'OHI_API_TOKEN' not in gateway
    assert 'OPENAI_API_KEY' not in backend and 'OPENAI_API_KEY' not in gateway
    assert gateway['HOME'] == str(state / 'home')
    assert gateway['HOME'] != os.environ.get('HOME')
    assert gateway.get('DB_PATH') is None
    assert backend['DB_PATH'] == str(state / 'fixture.db')
    assert (state.stat().st_mode & 0o777) == 0o700
    assert ((state / 'human.key').stat().st_mode & 0o777) == 0o600
    config = json.loads((state / 'home/.openclaw-ohi-test-proposals/openclaw.json').read_text())
    assert config['tools']['allow'] == ['openhouse_propose_lead']
    assert config['agents']['entries']['native-proposals']['tools']['allow'] == ['openhouse_propose_lead']
    assert backend['OHI_API_TOKEN'] not in json.dumps(config)
    assert not list(tmp_path.glob('.openclaw*'))


def test_socket_reservation_refuses_an_occupied_port_without_stopping_its_owner():
    h = helper()
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0)); occupied.listen()
        with pytest.raises(OSError):
            h.reserve(occupied.getsockname()[1])
        assert occupied.fileno() >= 0


def test_teardown_stops_only_its_own_child_and_is_bounded():
    h = helper()
    import subprocess
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    h.stop_children([child])
    assert child.poll() is not None


def test_read_acceptance_has_its_own_read_only_profile_and_synthetic_rows(tmp_path):
    h = helper()
    state = tmp_path / 'read-profile'
    h.prepare(state, 'ohi-test-read', 18083, 18883, kind='read')
    config = json.loads((state / 'home/.openclaw-ohi-test-read/openclaw.json').read_text())
    assert config['tools']['allow'] == ['openhouse_crm']
    assert list(config['agents']['entries']) == ['native-read']
    assert config['agents']['entries']['native-read']['tools']['allow'] == ['openhouse_crm']
    backend, gateway = h.environments(state)
    assert 'NATIVE_PROPOSAL_GATEWAY_URL' not in backend
    assert backend['NATIVE_READ_GATEWAY_URL'] == 'http://127.0.0.1:18883'
    assert 'OHI_API_TOKEN' not in gateway
    import sqlite3
    with sqlite3.connect(state / 'fixture.db') as conn:
        assert conn.execute('select count(*) from leads').fetchone()[0] == 37
        assert conn.execute('select count(*) from pending_changes').fetchone()[0] == 0


def test_cli_defaults_stay_in_private_home_and_reject_invalid_options(tmp_path):
    import subprocess
    original_home = os.environ.get('HOME')
    private_home = tmp_path / 'operator-home'
    private_home.mkdir()
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    env['HOME'] = str(private_home)
    command = [sys.executable, str(ROOT / 'scripts/native_proposal_acceptance.py')]
    prepared = subprocess.run(command + ['prepare'], cwd=tmp_path, env=env, capture_output=True, timeout=20)
    assert prepared.returncode == 0, prepared.stderr.decode()
    state = private_home / '.ohi-native-acceptance/ohi-native-proposals'
    assert json.loads((state / 'fixture.json').read_text()) == {
        'profile': 'ohi-native-proposals', 'port': 18082, 'gateway_port': 18882, 'kind': 'proposal'}
    for args in [['--profile', '../original'], ['--port', '18082', '--gateway-port', '18082'], ['--seconds', '0']]:
        result = subprocess.run(command + ['prepare'] + args, cwd=tmp_path, env=env, capture_output=True, timeout=5)
        assert result.returncode == 2
    assert not (tmp_path / 'original').exists()
    assert os.environ.get('HOME') == original_home
