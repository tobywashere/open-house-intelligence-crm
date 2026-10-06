"""Pinned local runtime contract and credential-separated OpenClaw configuration."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from .errors import InstallError

NODE = '24.15.0'
OPENCLAW = '2026.8.1-beta.3'
OLLAMA = '0.32.15'
MODEL = 'qwen3.5:9b'
DIGEST = '6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'
PROFILE = {'read': 'ohi-native-read', 'proposal': 'ohi-native-proposals'}
AGENT = {'read': 'native-read', 'proposal': 'native-proposals'}
PLUGIN = {'read': 'openhouse-read', 'proposal': 'openhouse-proposals'}
TOOL = {'read': 'openhouse_crm', 'proposal': 'openhouse_propose_lead'}


@dataclass(frozen=True)
class InstallOptions:
    root: Path
    state: Path
    openclaw: Path
    node: Path
    ports: tuple[int, int, int] = (18080, 18880, 18881)

    def __post_init__(self):
        if len(self.ports) != 3 or len(set(self.ports)) != 3 or any(type(p) is not int or not 1024 <= p <= 65535 for p in self.ports):
            raise ValueError('Use three distinct ports from 1024 through 65535.')
        for name in ('root', 'state', 'openclaw', 'node'):
            value = getattr(self, name)
            if not value.is_absolute():
                raise ValueError('Installation paths must be absolute.')


def base_environment(options, home):
    return {'PATH': os.pathsep.join(dict.fromkeys([str(options.node.parent), str(options.openclaw.parent), '/usr/bin', '/bin'])),
            'HOME': str(home), 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}


def child_environments(options, secrets):
    backend = base_environment(options, options.state/'backend-home')
    backend.update(DB_PATH=str(options.state/'crm.db'), PYTHONPATH=str(options.root/'backend'),
                   AGENT_MODE='mock', OHI_NATIVE_ONLY='1', INTEGRATIONS_MODE='off', INTEGRATIONS_POLLER='off',
                   OHI_API_TOKEN=secrets['human'], OHI_AGENT_API_TOKEN=secrets['agent'])
    envs = {'backend': backend}
    for index, kind in enumerate(('read', 'proposal'), 1):
        envs[kind] = {**base_environment(options, options.state/kind/'home'), 'OHI_AGENT_API_TOKEN': secrets['agent']}
        prefix = 'NATIVE_READ' if kind == 'read' else 'NATIVE_PROPOSAL'
        backend[prefix+'_GATEWAY_URL'] = f'http://127.0.0.1:{options.ports[index]}'
        backend[prefix+'_GATEWAY_TOKEN'] = secrets[kind]
        backend[prefix+'_AGENT_ID'] = AGENT[kind]
    return envs


def gateway_config(options, kind, secret):
    agent, plugin, tool = AGENT[kind], PLUGIN[kind], TOOL[kind]
    workspace = options.state/kind/'workspace'
    runtime = options.state/kind/'home'/('.openclaw-'+PROFILE[kind])
    return {
        'models': {'mode': 'replace', 'providers': {'ollama': {
            'api': 'ollama', 'apiKey': 'local-ollama', 'baseUrl': 'http://127.0.0.1:11434',
            'models': [{'id': MODEL, 'name': MODEL, 'reasoning': True, 'input': ['text', 'image'],
                        'contextWindow': 16384, 'maxTokens': 2048, 'params': {'num_ctx': 16384},
                        'compat': {'supportsTools': True, 'supportsUsageInStreaming': True,
                                   'supportsJsonSchemaResponseFormat': True}}]}}},
        'agents': {'defaults': {'workspace': str(workspace), 'skipBootstrap': True,
                              'model': {'primary': 'ollama/'+MODEL, 'fallbacks': []}},
                   'entries': {agent: {'workspace': str(workspace),
                                      'agentDir': str(runtime/'agents'/agent/'agent'), 'skills': [],
                                      'thinkingDefault': 'off', 'experimental': {'localModelLean': False},
                                      'tools': {'profile': 'full', 'allow': [tool]}}}},
        'tools': {'profile': 'full', 'allow': [tool]},
        'plugins': {'allow': ['ollama', plugin], 'load': {'paths': [str(options.root/'openclaw-plugins'/plugin)]},
                    'entries': {'ollama': {'enabled': True}, plugin: {'enabled': True,
                        'config': {'agentId': agent, 'crmApiUrl': f'http://127.0.0.1:{options.ports[0]}/api'}}},
                    'slots': {'memory': 'none'}},
        'gateway': {'mode': 'local', 'bind': 'loopback', 'port': options.ports[1 if kind == 'read' else 2],
                    'auth': {'mode': 'token', 'token': secret},
                    'http': {'endpoints': {'chatCompletions': {'enabled': True}}}},
        'logging': {'level': 'info', 'file': str(options.state/'logs'/(kind+'-gateway.log'))},
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)


def local_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def local_json(url, *, token=None, body=None, timeout=3):
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or parts.username or parts.password:
        raise InstallError('invalid_endpoint', 'A loopback HTTP endpoint is required.')
    headers = {'Accept': 'application/json'}
    if token: headers['X-API-Token'] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode(); headers['Content-Type'] = 'application/json'
    try:
        with local_opener().open(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as response:
            payload = response.read(1_048_577)
        if len(payload) > 1_048_576: raise ValueError()
        result = json.loads(payload)
        if not isinstance(result, dict): raise ValueError()
        return result
    except (OSError, ValueError, urllib.error.URLError):
        raise InstallError('local_service_unavailable', 'Local service unavailable or invalid response; check the documented runtime and private logs.') from None


def host_identity():
    try:
        info = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    except OSError: info = {}
    if platform.system() == 'Linux' and platform.machine() == 'x86_64' and sys.version_info[:2] == (3, 12) and info.get('ID', '').strip('"') == 'ubuntu' and info.get('VERSION_ID', '').strip('"') == '24.04':
        return 'ubuntu-24.04-x86_64-python3.12'
    return 'unsupported'


def executable_version(binary, env):
    try:
        result = subprocess.run([str(binary), '--version'], env=env, cwd=env['HOME'],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=True)
        if len(result.stdout) > 4096: raise ValueError()
        return result.stdout.decode().strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        raise InstallError('missing_runtime', 'Selected runtime cannot report its version; install the pinned executable from the guide.') from None


def preflight(options):
    if host_identity() != 'ubuntu-24.04-x86_64-python3.12':
        raise InstallError('unsupported_host', 'First-run setup requires Ubuntu 24.04 x86_64 and Python 3.12 (WSL2 supported).')
    with tempfile.TemporaryDirectory(prefix='ohi-preflight-') as home:
        env = base_environment(options, home)
        node = executable_version(options.node, env)
        claw = executable_version(options.openclaw, env)
    if node != 'v'+NODE:
        raise InstallError('node_mismatch', 'Select the pinned Node '+NODE+' executable.')
    if not re.fullmatch(r'(?:OpenClaw\s+)?'+re.escape(OPENCLAW)+r'(?:\s+\(5831b80\))?', claw):
        raise InstallError('openclaw_mismatch', 'Select the pinned OpenClaw '+OPENCLAW+' executable.')
    if local_json('http://127.0.0.1:11434/api/version').get('version') != OLLAMA:
        raise InstallError('ollama_mismatch', 'The local Ollama server must be version '+OLLAMA+'.')
    models = local_json('http://127.0.0.1:11434/api/tags').get('models')
    if not isinstance(models, list) or not any(isinstance(m, dict) and m.get('name') == MODEL and m.get('digest') == DIGEST for m in models):
        raise InstallError('model_mismatch', 'Install the documented model and verify its digest before setup/start.')
    return {'host': host_identity(), 'node': NODE, 'openclaw': OPENCLAW, 'ollama': OLLAMA, 'model_digest': DIGEST}
