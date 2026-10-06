#!/usr/bin/env python3
"""Build real tracked UI inputs with hostile dotenv/ambient API destinations.

Uses already npm-ci-installed worktree dependencies; does not install Python or
Node packages. --inject-dotenv is a test mutation that MUST fail the assertion.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from ohi_native.runtime import InstallOptions
from ohi_native import setup

ROOT=Path(__file__).resolve().parents[1]
CHECKOUT_URL='https://checkout-api.invalid'
AMBIENT_URL='https://ambient-api.invalid'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inject-dotenv',action='store_true')
    args=parser.parse_args()
    dependencies=ROOT/'dashboard/node_modules'
    if not (dependencies/'vite').is_dir():raise SystemExit('Run npm --prefix dashboard ci first.')
    node=shutil.which('node')
    if not node:raise SystemExit('Node is required for the real build check.')
    with tempfile.TemporaryDirectory(prefix='ohi-real-build-') as directory:
        base=Path(directory).resolve();root=base/'repo';root.mkdir()
        setup.copy_dashboard(ROOT,root/'dashboard')
        for name in ('.env','.env.production'):
            (root/'dashboard'/name).write_text('VITE_API_URL='+CHECKOUT_URL+'\n')
        subprocess.run(['git','init','-q',str(root)],check=True)
        subprocess.run(['git','-C',str(root),'add','-f','dashboard'],check=True)
        (root/'.venv-native/bin').mkdir(parents=True)
        (root/'.venv-native/bin/python').symlink_to(sys.executable)
        options=InstallOptions(root,base/'state',Path('/missing/openclaw'),Path(node).absolute())
        actual=setup.checked;builds=[]
        def checked(command,**kwargs):
            command=[str(x) for x in command]
            if command[1:4]==['-m','pip','install']:return # Dependency acquisition is separately verified.
            if command[1:]==['ci','--no-audit','--no-fund']:
                (kwargs['cwd']/'node_modules').symlink_to(dependencies,target_is_directory=True)
                return
            if command[1:]==['run','build']:
                assert 'VITE_API_URL' not in kwargs['env']
                assert not list(kwargs['cwd'].glob('.env*'))
                if args.inject_dotenv:
                    (kwargs['cwd']/'.env.production').write_text('VITE_API_URL='+CHECKOUT_URL+'\n')
                builds.append(command)
                return actual(command,**kwargs)
            raise AssertionError('Unexpected preparation command')
        setup.checked=checked
        old=os.environ.get('VITE_API_URL');os.environ['VITE_API_URL']=AMBIENT_URL
        try:setup.prepare_dependencies(options,base/'build.log')
        finally:
            setup.checked=actual
            if old is None:os.environ.pop('VITE_API_URL',None)
            else:os.environ['VITE_API_URL']=old
        assert len(builds)==1
        output='\n'.join(p.read_text() for p in (root/'dashboard/dist/assets').glob('*.js'))
        assert CHECKOUT_URL not in output and AMBIENT_URL not in output, 'Built dashboard contains an adversarial API destination'
        assert re.search(r'[A-Za-z_$][\w$]*="/api"',output), 'Built dashboard lacks the production relative API base'
        print('PASS: actual Vite output uses relative /api and excludes both adversarial API destinations')


if __name__=='__main__':main()
