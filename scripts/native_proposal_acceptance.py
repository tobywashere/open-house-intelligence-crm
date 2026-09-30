#!/usr/bin/env python3
"""Private synthetic WSL acceptance profile; never an installer or service manager.

Run stays in the foreground for at most --seconds and stops only its own process
children on exit. No original HOME/profile/config/data is supplied to either child.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def private_write(path, text):
    with path.open('x') as stream:
        path.chmod(0o600)
        stream.write(text)


def prepare(state, profile, port, gateway_port, kind='proposal'):
    state.mkdir(parents=True, mode=0o700, exist_ok=False)
    private_home = state / 'home'
    private_home.mkdir(mode=0o700)
    runtime = private_home / ('.openclaw-' + profile)
    runtime.mkdir(parents=True, mode=0o700)
    workspace = state / 'workspace'
    workspace.mkdir(mode=0o700)
    private_write(workspace / 'AGENTS.md',
        'Use only openhouse_crm for unfiltered directory reads. This agent cannot change records.\n' if kind == 'read' else
        'Use only openhouse_propose_lead to propose the requested lead name and optional email/phone. '
        'A proposal requires human review. Never claim a lead was created or approved.\n')
    human, agent, gateway = (secrets.token_hex(32) for _ in range(3))
    private_write(state / 'human.key', human + '\n')
    private_write(state / 'agent.key', agent + '\n')
    private_write(state / 'gateway.key', gateway + '\n')
    private_write(state / 'fixture.json', json.dumps({
        'profile': profile, 'port': port, 'gateway_port': gateway_port, 'kind': kind,
    }) + '\n')
    tool = 'openhouse_crm' if kind == 'read' else 'openhouse_propose_lead'
    agent_id = 'native-read' if kind == 'read' else 'native-proposals'
    plugin = 'openhouse-read' if kind == 'read' else 'openhouse-proposals'
    config = {
        'models': {'mode': 'replace', 'providers': {'ollama': {
            'api': 'ollama', 'apiKey': 'local-ollama', 'baseUrl': 'http://127.0.0.1:11434',
            'models': [{'id': 'qwen3.5:9b', 'name': 'qwen3.5:9b', 'reasoning': True,
                'input': ['text', 'image'], 'contextWindow': 16384, 'maxTokens': 2048,
                'params': {'num_ctx': 16384}, 'compat': {'supportsTools': True,
                    'supportsUsageInStreaming': True, 'supportsJsonSchemaResponseFormat': True}}],
        }}},
        'agents': {'defaults': {'workspace': str(workspace), 'skipBootstrap': True,
            'model': {'primary': 'ollama/qwen3.5:9b', 'fallbacks': []}}, 'entries': {
                agent_id: {'workspace': str(workspace),
                    'agentDir': str(runtime / ('agents/' + agent_id + '/agent')), 'skills': [],
                    'thinkingDefault': 'off', 'experimental': {'localModelLean': False},
                    'tools': {'profile': 'full', 'allow': [tool]}},
            }},
        'tools': {'profile': 'full', 'allow': [tool]},
        'plugins': {'allow': ['ollama', plugin],
            'load': {'paths': [str(ROOT / 'openclaw-plugins' / plugin)]},
            'entries': {'ollama': {'enabled': True}, plugin: {
                'enabled': True, 'config': {'agentId': agent_id,
                    'crmApiUrl': f'http://127.0.0.1:{port}/api'}}}, 'slots': {'memory': 'none'}},
        'gateway': {'mode': 'local', 'bind': 'loopback', 'port': gateway_port,
            'auth': {'mode': 'token', 'token': gateway},
            'http': {'endpoints': {'chatCompletions': {'enabled': True}}}},
        'logging': {'level': 'info', 'file': str(state / 'gateway.log')},
    }
    private_write(runtime / 'openclaw.json', json.dumps(config, indent=2) + '\n')
    backend_env, _ = environments(state)
    subprocess.run([sys.executable, '-c', 'from app.db import init_db; init_db()'],
                   cwd=workspace, env=backend_env, check=True, timeout=20,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if kind == 'read':
        with sqlite3.connect(state / 'fixture.db') as conn:
            conn.executemany('insert into leads(name,status,source) values (?,?,?)',
                [(f'Synthetic Lead {i + 1:02}', 'new', 'acceptance') for i in range(37)])
    (state / 'fixture.db').chmod(0o600)
    snapshot(state)


def environments(state):
    settings = json.loads((state / 'fixture.json').read_text())
    common = {k: os.environ[k] for k in ['PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'] if k in os.environ}
    common['HOME'] = str(state / 'home')
    agent = (state / 'agent.key').read_text().strip()
    # Gateway sees only the agent CRM credential. Its separate gateway secret is
    # in its private profile; human.key and the DB stay outside that private HOME.
    gateway = {**common, 'OHI_AGENT_API_TOKEN': agent}
    prefix = 'NATIVE_READ' if settings['kind'] == 'read' else 'NATIVE_PROPOSAL'
    backend = {**common, 'DB_PATH': str(state / 'fixture.db'),
        'PYTHONPATH': str(ROOT / 'backend'), 'PYTHONDONTWRITEBYTECODE': '1',
        'AGENT_MODE': 'mock', 'INTEGRATIONS_MODE': 'off', 'INTEGRATIONS_POLLER': 'off',
        'OHI_API_TOKEN': (state / 'human.key').read_text().strip(), 'OHI_AGENT_API_TOKEN': agent,
        prefix + '_GATEWAY_URL': f'http://127.0.0.1:{settings["gateway_port"]}',
        prefix + '_GATEWAY_TOKEN': (state / 'gateway.key').read_text().strip(),
        prefix + '_AGENT_ID': 'native-read' if settings['kind'] == 'read' else 'native-proposals'}
    return backend, gateway


def reserve(port):
    listener = socket.socket()
    try:
        listener.bind(('127.0.0.1', port))
        listener.listen()
        return listener
    except BaseException:
        listener.close()
        raise


def stop_children(children):
    for child in reversed(children):
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    for child in reversed(children):
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def snapshot(state):
    # Only counts and a content hash leave the disposable DB; never credentials.
    with sqlite3.connect('file:' + str(state / 'fixture.db') + '?mode=ro', uri=True) as conn:
        tables = ['leads', 'events', 'pending_changes', 'appointments', 'reminders', 'native_lead_requests']
        data = {table: conn.execute('select * from ' + table + ' order by rowid').fetchall() for table in tables}
    print(json.dumps({'counts': {k: len(v) for k, v in data.items()},
        'content_sha256': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()}), flush=True)


def run(state, seconds):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Live acceptance runs in WSL/Linux with the already installed pinned runtime.')
    if not (ROOT / 'dashboard/dist/index.html').is_file():
        raise RuntimeError('Build dashboard/dist before acceptance.')
    executable = shutil.which('openclaw')
    if not executable:
        raise RuntimeError('Existing OpenClaw runtime is missing; this helper never installs it.')
    settings = json.loads((state / 'fixture.json').read_text())
    backend_env, gateway_env = environments(state)
    children = []
    with ExitStack() as stack:
        backend_socket = stack.enter_context(reserve(settings['port']))
        gateway_socket = stack.enter_context(reserve(settings['gateway_port']))
        # Validation runs inside the isolated HOME and workspace, with no human key.
        validation = subprocess.run([executable, '--profile', settings['profile'], 'config', 'validate'],
            cwd=state / 'workspace', env=gateway_env, capture_output=True, timeout=20)
        if validation.returncode:
            raise RuntimeError('Isolated gateway config validation failed; live acceptance is not ready.')
        try:
            for name, env, command, fds in [
                ('backend', backend_env, [sys.executable, '-m', 'uvicorn', 'app.main:app',
                    '--fd', str(backend_socket.fileno()), '--log-level', 'warning'], (backend_socket.fileno(),)),
                ('gateway', gateway_env, [executable, '--profile', settings['profile'], 'gateway',
                    'run', '--port', str(settings['gateway_port']), '--bind', 'loopback'], ()),
            ]:
                log = stack.enter_context((state / (name + '-stdout.log')).open('a'))
                (state / (name + '-stdout.log')).chmod(0o600)
                if name == 'gateway':
                    # OpenClaw does not consume an inherited listening fd. Refuse
                    # occupied ports before launch; release only for our own child.
                    gateway_socket.close()
                children.append(subprocess.Popen(command, env=env, cwd=state / 'workspace',
                    pass_fds=fds, stdout=log, stderr=log, start_new_session=True))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 30
            while True:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError('An isolated child exited; inspect only the private fixture logs.')
                try:
                    with opener.open(f'http://127.0.0.1:{settings["port"]}/api/health', timeout=.5) as response:
                        if response.status == 200:
                            with socket.create_connection(('127.0.0.1', settings['gateway_port']), timeout=.5):
                                break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError('Isolated services did not become ready in 30 seconds.')
                time.sleep(.2)
            print(f'Isolated fixture: http://127.0.0.1:{settings["port"]}; human key: {state / "human.key"}', flush=True)
            print(f'Live inference acceptance is pending your checks. Ctrl-C stops these children; limit {seconds}s.', flush=True)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError('An isolated child exited during acceptance.')
                time.sleep(.2)
        finally:
            stop_children(children)


def interrupted(*_):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'snapshot'])
    parser.add_argument('--kind', choices=['read', 'proposal'], default='proposal')
    parser.add_argument('--profile', default='ohi-native-proposals')
    parser.add_argument('--port', type=int, default=18082)
    parser.add_argument('--gateway-port', type=int, default=18882)
    parser.add_argument('--seconds', type=int, default=1800)
    args = parser.parse_args()
    if not re.fullmatch(r'ohi-[a-z0-9][a-z0-9-]{0,48}', args.profile):
        parser.error('Use an isolated ohi- profile name with lowercase letters, numbers and hyphens.')
    if not (1024 <= args.port <= 65535 and 1024 <= args.gateway_port <= 65535 and args.port != args.gateway_port):
        parser.error('Use two distinct unprivileged ports.')
    if not 1 <= args.seconds <= 3600:
        parser.error('seconds must be 1–3600')
    state = Path.home() / '.ohi-native-acceptance' / args.profile
    os.umask(0o077)
    if args.action == 'prepare':
        prepare(state, args.profile, args.port, args.gateway_port, args.kind)
        print('Prepared private synthetic fixture:', state)
    elif args.action == 'snapshot':
        snapshot(state)
    else:
        signal.signal(signal.SIGTERM, interrupted)
        try:
            run(state, args.seconds)
        except KeyboardInterrupt:
            print('Stopped the isolated acceptance children.')


if __name__ == '__main__':
    main()
