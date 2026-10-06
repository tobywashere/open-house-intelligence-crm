"""Private durable state. Validation never repairs, rotates or mutates it."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone

from .errors import InstallError
from .runtime import PROFILE


def validate_path(path):
    if not path.is_absolute() or '..' in path.parts:
        raise InstallError('unsafe_path', 'Use an absolute state path without parent traversal.')
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise InstallError('unsafe_path', 'Symlinked installation paths are not supported.')
        if part != path and part.exists() and not part.is_dir():
            raise InstallError('unsafe_path', 'An installation ancestor is not a directory.')


def private_dir(path):
    validate_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info=path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise InstallError('private_permissions', 'Installation directories must be owned by you with mode 0700.')


def write_private(path, content):
    validate_path(path)
    fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())


def read_private(path):
    validate_path(path)
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'r') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 1_048_576:
                raise ValueError()
            return stream.read()
    except (OSError,ValueError,UnicodeError):
        raise InstallError('private_file_invalid','A private installation file is missing, unsafe or has wrong permissions; restore the matching installation.') from None


def atomic_json(path,data):
    encoded=json.dumps(data,indent=2,sort_keys=True)+'\n'
    validate_path(path)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


@contextmanager
def exclusive_lock(state):
    validate_path(state)
    private_dir(state.parent/('.'+state.name+'-locks'))
    lock=state.parent/('.'+state.name+'-locks')/'operation.lock'
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
            raise InstallError('unsafe_lock','Installation lock has unsafe ownership or permissions.')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError('busy','Another setup/start operation owns this installation; stop it before continuing.') from None
        yield
    finally:
        os.close(fd)


def load_manifest(state):
    validate_path(state)
    try:
        info=state.stat()
        if not state.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:raise ValueError()
        data=json.loads(read_private(state/'manifest.json'))
        if not isinstance(data,dict) or data.get('schema_version') != 1 or data.get('complete') is not True:raise ValueError()
        return data
    except (OSError,ValueError):
        raise InstallError('incomplete_install','This is not a completed matching installation; keep it for inspection and use a new empty state path.') from None


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):h.update(block)
    return h.hexdigest()


def source_identity(root):
    try:
        revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],stderr=subprocess.DEVNULL,text=True,timeout=5).strip()
        names=subprocess.check_output(['git','-C',str(root),'ls-files','-z','--','backend','scripts','dashboard','openclaw-plugins','.env.example','.github'],stderr=subprocess.DEVNULL,timeout=5).decode().split('\0')
        h=hashlib.sha256()
        for name in sorted(n for n in names if n):
            path=root/name
            if path.is_symlink() or not path.is_file():raise ValueError()
            h.update(name.encode()+b'\0'+digest(path).encode()+b'\0')
        return {'root':str(root),'revision':revision,'digest':h.hexdigest()}
    except (OSError,ValueError,subprocess.SubprocessError):
        raise InstallError('source_unavailable','Use the original intact Git checkout at its prepared revision.') from None


def build_digest(root):
    directory=root/'dashboard/dist'
    if not (directory/'index.html').is_file() or not (directory/'assets').is_dir():
        raise InstallError('build_missing','Prepare a fresh dashboard build with setup.')
    h=hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():raise InstallError('unsafe_build','Dashboard build cannot contain symlinks.')
        if path.is_file():h.update(str(path.relative_to(directory)).encode()+b'\0'+digest(path).encode())
    return h.hexdigest()


def config_files():
    return [*(name+'.key' for name in ('human','agent','read','proposal')),
            *(f'{kind}/home/.openclaw-{PROFILE[kind]}/openclaw.json' for kind in PROFILE),
            *(f'{kind}/workspace/AGENTS.md' for kind in PROFILE)]


def read_secrets(state):
    keys={name:read_private(state/(name+'.key')).strip() for name in ('human','agent','read','proposal')}
    if len(set(keys.values())) != 4 or any(not re.fullmatch('[a-f0-9]{64}',value) for value in keys.values()):
        raise InstallError('invalid_credentials','Installation credentials must be distinct generated keys; restore the original private state.')
    return keys


def make_manifest(options,runtimes):
    return {'schema_version':1,'complete':True,'source':source_identity(options.root),
            'state':str(options.state),'ports':list(options.ports),'openclaw':str(options.openclaw),'node':str(options.node),
            'runtime':runtimes,'prepared_at':datetime.now(timezone.utc).isoformat(),
            'build_digest':build_digest(options.root),
            'files':{name:digest(options.state/name) for name in config_files()},
            'locks':{name:digest(options.root/name) for name in ('backend/requirements-native.lock','dashboard/package-lock.json')}}


def validate_install(options):
    data=load_manifest(options.state)
    try:
        for name in ('logs','backend-home','read','read/home','read/workspace','proposal','proposal/home','proposal/workspace',
                     'read/home/.openclaw-ohi-native-read','proposal/home/.openclaw-ohi-native-proposals'):
            path=options.state/name;validate_path(path);info=path.stat()
            if not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:raise ValueError()
        for name in config_files():read_private(options.state/name)
        read_secrets(options.state)
        db=options.state/'crm.db';validate_path(db);info=db.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:raise ValueError()
        expected=make_manifest(options,data['runtime'])
        for field in ('source','state','ports','openclaw','node','build_digest','files','locks'):
            if data.get(field) != expected[field]:raise ValueError()
        return data
    except (OSError,KeyError,ValueError,TypeError):
        raise InstallError('installation_changed','Prepared source, build, paths or private files changed; restore matching files. Setup does not reset or upgrade existing state.') from None
