"""State preservation and exclusive ownership on real temporary files."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))


def state_module():
    assert (ROOT/'scripts/ohi_native/state.py').exists(), 'native state implementation missing'
    return importlib.import_module('ohi_native.state')


def test_private_write_never_replaces_existing(tmp_path):
    s=state_module();p=tmp_path/'human.key'
    s.write_private(p,'first')
    with pytest.raises(FileExistsError):s.write_private(p,'second')
    assert p.read_text()=='first'
    assert p.stat().st_mode&0o777==0o600


def test_symlink_and_unrelated_directory_refused(tmp_path):
    s=state_module();target=tmp_path/'original';target.mkdir();(target/'keep').write_text('original')
    link=tmp_path/'link';link.symlink_to(target,target_is_directory=True)
    with pytest.raises(s.InstallError):s.validate_path(link/'state')
    with pytest.raises(s.InstallError):s.load_manifest(target)
    assert (target/'keep').read_text()=='original'


def test_private_reader_rejects_permissive_file_and_symlink(tmp_path):
    s=state_module();p=tmp_path/'secret';p.write_text('secret');p.chmod(0o644)
    with pytest.raises(s.InstallError):s.read_private(p)
    p.chmod(0o600);link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(s.InstallError):s.read_private(link)
    assert s.read_private(p)=='secret'


@pytest.mark.parametrize('data',[{}, {'schema_version':1,'complete':False},{'schema_version':2,'complete':True}])
def test_incomplete_or_unknown_manifest_refused(tmp_path,data):
    s=state_module();tmp_path.chmod(0o700);s.write_private(tmp_path/'manifest.json',json.dumps(data))
    with pytest.raises(s.InstallError):s.load_manifest(tmp_path)


def test_lock_prevents_second_process_and_releases_on_exit(tmp_path):
    s=state_module();p=tmp_path/'new-state'
    command=[sys.executable,'-c',
             'from pathlib import Path; from ohi_native.state import exclusive_lock; import sys; '\
             '\nwith exclusive_lock(Path(sys.argv[1])): print("owned")',str(p)]
    env={**os.environ,'PYTHONPATH':str(ROOT/'scripts')}
    with s.exclusive_lock(p):
        failed=subprocess.run(command,env=env,capture_output=True,text=True,timeout=5)
        assert failed.returncode!=0 and 'owned' not in failed.stdout
    succeeded=subprocess.run(command,env=env,capture_output=True,text=True,timeout=5)
    assert succeeded.returncode==0 and succeeded.stdout.strip()=='owned'
    assert not p.exists()


def test_manifest_atomic_write_preserves_previous_on_serialization_failure(tmp_path):
    s=state_module();p=tmp_path/'manifest.json';s.write_private(p,'old')
    with pytest.raises(TypeError):s.atomic_json(p, {'not_serializable':object()})
    assert p.read_text()=='old'
    s.atomic_json(p,{'schema_version':1,'complete':True})
    assert json.loads(s.read_private(p))['complete'] is True


def installation(tmp_path):
    s=state_module()
    from ohi_native.runtime import InstallOptions
    import sqlite3
    root=tmp_path/'repo';root.mkdir()
    import venv
    venv.EnvBuilder(with_pip=False).create(root/'.venv-native')
    (root/'backend').mkdir();(root/'backend/requirements-native.lock').write_text('locked')
    (root/'dashboard/dist/assets').mkdir(parents=True)
    (root/'dashboard/package-lock.json').write_text('{}')
    (root/'dashboard/dist/index.html').write_text('<html/>')
    (root/'dashboard/dist/assets/app.js').write_text('app')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','backend','dashboard/package-lock.json'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
    state=tmp_path/'state';state.mkdir(mode=0o700)
    for name,letter in zip(('human','agent','read','proposal'),'abcd'):s.write_private(state/(name+'.key'),letter*64)
    (state/'logs').mkdir(mode=0o700)
    (state/'backend-home').mkdir(mode=0o700)
    for kind,profile in [('read','ohi-native-read'),('proposal','ohi-native-proposals')]:
        (state/kind/'home'/('.openclaw-'+profile)).mkdir(parents=True,mode=0o700)
        (state/kind).chmod(0o700);(state/kind/'home').chmod(0o700)
        (state/kind/'workspace').mkdir(mode=0o700)
        s.write_private(state/kind/'home'/('.openclaw-'+profile)/'openclaw.json','{}')
        s.write_private(state/kind/'workspace/AGENTS.md','tool only')
    with sqlite3.connect(state/'crm.db') as db:db.execute('create table leads(id integer)')
    (state/'crm.db').chmod(0o600)
    o=InstallOptions(root,state,tmp_path/'openclaw',tmp_path/'node')
    s.atomic_json(state/'manifest.json',s.make_manifest(o,{'node':'24.15.0'}))
    return s,o


@pytest.mark.parametrize('change',['source','build','key','config','missing','directory_permissions','root_path','secret_duplicate'])
def test_validation_refuses_drift_without_repair(tmp_path,change):
    s,o=installation(tmp_path)
    assert s.validate_install(o)['complete'] is True
    if change=='source':(o.root/'backend/requirements-native.lock').write_text('changed')
    if change=='build':(o.root/'dashboard/dist/assets/app.js').write_text('changed')
    if change=='key':(o.state/'human.key').write_text('changed')
    if change=='config':(o.state/'read/home/.openclaw-ohi-native-read/openclaw.json').write_text('{"changed":true}')
    if change=='missing':(o.state/'agent.key').unlink()
    if change=='directory_permissions':(o.state/'read/workspace').chmod(0o755)
    if change=='root_path':
        from dataclasses import replace
        o=replace(o,root=tmp_path/'elsewhere')
    if change=='secret_duplicate':(o.state/'agent.key').write_text((o.state/'human.key').read_text())
    with pytest.raises(s.InstallError):s.validate_install(o)


@pytest.mark.parametrize('damage',['missing','configuration','interpreter','packages'])
def test_environment_damage_invalidates_prepared_install(tmp_path,damage):
    s,o=installation(tmp_path)
    env=o.root/'.venv-native'
    if damage=='missing':
        import shutil
        shutil.rmtree(env)
    elif damage=='configuration':
        (env/'pyvenv.cfg').write_text((env/'pyvenv.cfg').read_text()+'\nchanged = true\n')
    elif damage=='interpreter':
        (env/'bin/python').unlink();(env/'bin/python').write_text('broken')
    else:
        metadata=next((env/'lib').glob('python*/site-packages'))/'unexpected-1.0.dist-info'
        metadata.mkdir();(metadata/'METADATA').write_text('Name: unexpected\nVersion: 1.0\n')
    with pytest.raises(s.InstallError):s.validate_install(o)
