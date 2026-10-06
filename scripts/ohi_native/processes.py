"""Foreground supervision of only the children created by this invocation."""
from contextlib import ExitStack
import json
import os
import signal
import socket
import subprocess
import tempfile
import time

from .errors import InstallError
from .runtime import AGENT, PROFILE, child_environments, local_json, preflight
from .state import exclusive_lock, read_secrets, validate_install


def reserve_listener(port):
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        listener.bind(('127.0.0.1',port));listener.listen(128)
        return listener
    except BaseException:
        listener.close();raise


def signal_group(child, sig):
    # macOS reports EPERM for an unreaped zombie group leader. Reap it, then
    # retry the owned group so surviving descendants are still terminated.
    child.poll()
    try:
        os.killpg(child.pid,sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        if sig==0:return True # Group exists or is in the kernel exit transition.
        try:child.wait(timeout=.2)
        except subprocess.TimeoutExpired:raise
        try:os.killpg(child.pid,sig)
        except ProcessLookupError:return False
    return True


def stop_children(children,grace=5):
    # A parent may have exited while a descendant still owns its process group.
    for child in children:
        signal_group(child,signal.SIGTERM)
    deadline=time.monotonic()+grace
    while children and time.monotonic()<deadline:
        alive=False
        for child in children:
            alive=signal_group(child,0) or alive
        if not alive:break
        time.sleep(.025)
    for child in children:
        signal_group(child,signal.SIGKILL)
    for child in children:
        try:child.wait(timeout=2)
        except subprocess.TimeoutExpired:pass


def command_json(command,*,env,cwd,timeout=4):
    child=None
    # No diagnostic output is written into installation files or forwarded publicly.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            child=subprocess.Popen([str(x) for x in command],env=env,cwd=cwd,
                stdin=subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,umask=0o077)
            if child.wait(timeout=timeout)!=0:raise ValueError()
            output.seek(0);data=output.read(1_048_577)
            if len(data)>1_048_576:raise ValueError()
            value=json.loads(data)
            if not isinstance(value,dict):raise ValueError()
            return value
        except (OSError,ValueError,subprocess.SubprocessError):
            raise InstallError('gateway_unavailable','Gateway authentication/health failed; inspect its private log and pinned configuration.') from None
        finally:
            if child is not None:stop_children([child],grace=.1)


def gateway_health(options,kind,envs,timeout=4):
    # Pinned CLI implementation calls callGatewayReadOnlyCli(sharedStateMode='read-only').
    value=command_json([options.openclaw,'--profile',PROFILE[kind],'gateway','call','health',
                        '--port',str(options.ports[1 if kind=='read' else 2]),'--json','--timeout','1500'],
                       env=envs[kind],cwd=options.state/kind/'workspace',timeout=timeout)
    agents=value.get('agents')
    if value.get('ok') is not True or not isinstance(agents,list) or not any(isinstance(a,dict) and a.get('agentId')==AGENT[kind] for a in agents):
        raise InstallError('gateway_identity','Authenticated gateway did not report the expected native agent.')
    return True


def check_services(options,keys,envs=None,deadline=None):
    envs=envs or child_environments(options,keys)
    def remaining(limit):
        value=limit if deadline is None else min(limit,deadline-time.monotonic())
        if value<=0:raise InstallError('startup_timeout','Services did not become ready within 30 seconds.')
        return value
    status=local_json(f'http://127.0.0.1:{options.ports[0]}/api/auth/status',token=keys['human'],timeout=remaining(1))
    if status.get('mode')!='capabilities' or status.get('role')!='human' or status.get('workflow_mode')!='native':
        raise InstallError('backend_identity','Backend did not authenticate as the prepared native installation.')
    for kind in PROFILE:gateway_health(options,kind,envs,timeout=remaining(4))


def check_children(children):
    if any(child.poll() is not None for child in children):
        raise InstallError('child_exited','A required child exited; all owned services are stopping. Inspect private logs.')


def wait_ready(children,probe,stopping,timeout=30):
    deadline=time.monotonic()+timeout
    while not stopping():
        check_children(children)
        try:
            probe();check_children(children);return
        except InstallError:
            if time.monotonic()>=deadline:
                raise InstallError('startup_timeout','Services did not become ready within the startup deadline.') from None
        time.sleep(.1)


def supervise(children,stopping,interval=.2):
    while not stopping():
        check_children(children);time.sleep(interval)


def run_install(options):
    with exclusive_lock(options.state),exclusive_lock(options.root/'ohi-native-build'):
        manifest=validate_install(options)
        if preflight(options)!=manifest['runtime']:
            raise InstallError('runtime_changed','Prepared runtime changed; restore the pinned runtime.')
        python=options.root/'.venv-native/bin/python'
        if not python.is_file():raise InstallError('venv_missing','Dedicated Python environment is missing; restore the prepared checkout.')
        keys=read_secrets(options.state);envs=child_environments(options,keys)
        children=[];stopping=[False];old_handlers={}
        def stop(signum,frame):stopping[0]=True
        with ExitStack() as stack:
            try:
                listeners=[stack.enter_context(reserve_listener(p)) for p in options.ports]
            except OSError:
                raise InstallError('port_in_use','A selected port is already occupied. Stop only your own conflicting service or use a fresh installation with different ports.') from None
            try:
                for sig in (signal.SIGINT,signal.SIGTERM):
                    old_handlers[sig]=signal.signal(sig,stop)
                commands=[('backend',[python,'-m','uvicorn','app.main:app','--fd',str(listeners[0].fileno()),'--log-level','warning'],(listeners[0].fileno(),))]
                commands += [(kind,[options.openclaw,'--profile',PROFILE[kind],'gateway','run','--port',str(options.ports[index]),'--bind','loopback'],()) for index,kind in enumerate(PROFILE,1)]
                for index,(kind,command,fds) in enumerate(commands):
                    if stopping[0]:break
                    logfile=options.state/'logs'/(kind+'-stdout.log')
                    fd=os.open(logfile,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
                    log=stack.enter_context(os.fdopen(fd,'ab'))
                    if index: listeners[index].close() # Gateway CLI cannot inherit a socket.
                    cwd=options.state/'backend-home' if kind=='backend' else options.state/kind/'workspace'
                    children.append(subprocess.Popen([str(x) for x in command],env=envs[kind],cwd=cwd,
                        pass_fds=fds,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,umask=0o077))
                deadline=time.monotonic()+30
                wait_ready(children,lambda:check_services(options,keys,envs,deadline),lambda:stopping[0])
                if not stopping[0]:
                    print(json.dumps({'ready':True,'url':f'http://127.0.0.1:{options.ports[0]}',
                                     'source_revision':manifest['source']['revision'],'model_verified':False}),flush=True)
                    supervise(children,lambda:stopping[0])
            except OSError:
                raise InstallError('launch_failed','Could not start the prepared services; inspect private logs.') from None
            finally:
                stop_children(children)
                for sig,handler in old_handlers.items():signal.signal(sig,handler)
        return 0
