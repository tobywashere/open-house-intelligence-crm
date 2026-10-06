"""Prepare an empty persistent CRM without inheriting developer configuration."""
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

from .errors import InstallError
from .runtime import AGENT, PROFILE, base_environment, child_environments, gateway_config, preflight
from .state import (atomic_json, exclusive_lock, make_manifest, private_dir, read_secrets,
                    validate_install, validate_path, write_private)


def checked(command, *, cwd, env, log, timeout):
    try:
        with log.open('ab') as output:
            log.chmod(0o600)
            subprocess.run([str(x) for x in command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           stdout=output, stderr=output, timeout=timeout, check=True, umask=0o077)
    except (OSError, subprocess.SubprocessError):
        raise InstallError('preparation_failed', 'Preparation command failed; inspect the private setup log. Existing data has not been reset.') from None


def copy_dashboard(root, target):
    target.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        names=subprocess.check_output(['git','-C',str(root),'ls-files','-z','--','dashboard'],timeout=5).decode().split('\0')
        for name in filter(None,names):
            relative=Path(name).relative_to('dashboard')
            if any(part.startswith('.env') or part in ('node_modules','dist','dist.new','dist.old') for part in relative.parts):continue
            source=root/name
            if source.is_symlink() or not source.is_file():raise ValueError()
            dest=target/relative;dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            shutil.copyfile(source,dest)
    except (OSError,ValueError,subprocess.SubprocessError):
        raise InstallError('build_inputs_invalid','Build requires intact tracked dashboard sources without symlinks.') from None


def prepare_dependencies(options, log):
    venv=options.root/'.venv-native'
    validate_path(venv)
    with tempfile.TemporaryDirectory(prefix='.ohi-build-',dir=options.root) as directory:
        temp=Path(directory);home=temp/'home';home.mkdir(mode=0o700)
        env=base_environment(options,home)
        # Network settings apply only to explicit dependency acquisition.
        for key in ('HTTPS_PROXY','HTTP_PROXY','NO_PROXY','SSL_CERT_FILE','REQUESTS_CA_BUNDLE'):
            if key in os.environ:env[key]=os.environ[key]
        env.update(PIP_CONFIG_FILE=os.devnull, npm_config_userconfig=os.devnull,
                   npm_config_cache=str(temp/'npm-cache'), npm_config_audit='false')
        if not venv.exists():checked([sys.executable,'-m','venv',venv],cwd=temp,env=env,log=log,timeout=60)
        if not (venv/'bin/python').is_file():raise InstallError('venv_invalid','Dedicated .venv-native is incomplete; inspect it before preparing a fresh checkout.')
        checked([venv/'bin/python','-m','pip','install','--require-hashes','-r',options.root/'backend/requirements-native.lock'],
                cwd=temp,env=env,log=log,timeout=600)
        target=temp/'dashboard';copy_dashboard(options.root,target)
        npm=options.node.parent/'npm'
        checked([npm,'ci','--no-audit','--no-fund'],cwd=target,env=env,log=log,timeout=600)
        # Build environment excludes all network/proxy and user-config settings.
        build_env=base_environment(options,home)
        build_env.update(npm_config_userconfig=os.devnull,npm_config_cache=str(temp/'npm-cache'))
        checked([npm,'run','build'],cwd=target,env=build_env,log=log,timeout=180)
        output=target/'dist'
        if not (output/'index.html').is_file() or not (output/'assets').is_dir():
            raise InstallError('build_missing','The dashboard build did not produce a complete application.')
        dist=options.root/'dashboard/dist';validate_path(dist)
        if dist.exists():
            backup=options.root/'dashboard'/('dist.before-native-'+secrets.token_hex(4))
            dist.rename(backup) # Retain the previous build; never use it as fallback.
        output.rename(dist)


def render_profiles(options, physical_state, keys):
    for kind in PROFILE:
        workspace=physical_state/kind/'workspace'
        text=('Use only openhouse_crm for unfiltered directory reads. Never change records.\n' if kind=='read' else
              'Use only openhouse_propose_lead for the requested name and optional email/phone. Human approval is required. Never claim approval or creation.\n')
        agents=workspace/'AGENTS.md'
        if not agents.exists():write_private(agents,text)
        config=physical_state/kind/'home'/('.openclaw-'+PROFILE[kind])/'openclaw.json'
        atomic_json(config,gateway_config(options,kind,keys[kind]))


def validate_configs(options, physical_state, log):
    physical_options=replace(options,state=physical_state)
    envs=child_environments(physical_options,read_secrets(physical_state))
    for kind in PROFILE:
        checked([options.openclaw,'--profile',PROFILE[kind],'config','validate'],
                cwd=physical_state/kind/'workspace',env=envs[kind],log=log,timeout=30)


def initialize_database(options, physical_state, log):
    env=child_environments(replace(options,state=physical_state),read_secrets(physical_state))['backend']
    checked([options.root/'.venv-native/bin/python','-c','from app.db import init_db; init_db()'],
            cwd=physical_state/'backend-home',env=env,log=log,timeout=30)
    (physical_state/'crm.db').chmod(0o600)


def setup_install(options):
    validate_path(options.state)
    with exclusive_lock(options.state), exclusive_lock(options.root/'ohi-native-build'):
        if options.state.exists():
            manifest=validate_install(options)
            if preflight(options)!=manifest['runtime']:
                raise InstallError('runtime_changed','Prepared runtime changed; restore the pinned runtime before starting.')
            return {'state':str(options.state),'source_revision':manifest['source']['revision'],'created':False,
                    'next_command':f'python3 scripts/native_local.py start --state {str(options.state)!r}'}
        runtimes=preflight(options)
        # Preflight has no state mutation; lock and incomplete preparation stay private.
        stage=Path(tempfile.mkdtemp(prefix='.'+options.state.name+'.incomplete-',dir=options.state.parent))
        log=stage/'setup.log'
        try:
            for name in ('logs','backend-home','read','read/home','read/workspace','read/home/.openclaw-ohi-native-read',
                         'proposal','proposal/home','proposal/workspace','proposal/home/.openclaw-ohi-native-proposals'):
                private_dir(stage/name)
            keys={name:secrets.token_hex(32) for name in ('human','agent','read','proposal')}
            for name,value in keys.items():write_private(stage/(name+'.key'),value+'\n')
            prepare_dependencies(options,log)
            render_profiles(replace(options,state=stage),stage,keys)
            validate_configs(options,stage,log)
            initialize_database(options,stage,log)
            render_profiles(options,stage,keys)
            stage.rename(options.state)
            try:
                validate_configs(options,options.state,options.state/'setup.log')
                manifest=make_manifest(options,runtimes)
                atomic_json(options.state/'manifest.json',manifest)
                validate_install(options)
            except BaseException:
                options.state.rename(stage)
                raise
            return {'state':str(options.state),'source_revision':manifest['source']['revision'],'created':True,
                    'next_command':f'python3 scripts/native_local.py start --state {str(options.state)!r}'}
        except InstallError as error:
            raise InstallError(error.code, f'{error} Incomplete private preparation retained at {stage}.') from None
        except (OSError,ValueError,subprocess.SubprocessError):
            raise InstallError('setup_incomplete',f'Setup is incomplete. Private preparation is retained at {stage}; use a new empty state path after resolving the cause.') from None
