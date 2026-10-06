#!/usr/bin/env python3
"""Prepare, start and inspect a private local OpenHouse installation."""
import argparse
import json
from pathlib import Path
import shutil
import sys

from ohi_native.errors import InstallError
from ohi_native.runtime import InstallOptions
from ohi_native.state import load_manifest

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['setup','start','doctor'])
    parser.add_argument('--state',type=Path,default=Path.home()/'.local/share/openhouse/native')
    parser.add_argument('--openclaw',type=Path)
    parser.add_argument('--node',type=Path)
    parser.add_argument('--port',type=int)
    parser.add_argument('--read-port',type=int)
    parser.add_argument('--proposal-port',type=int)
    parser.add_argument('--live-read',action='store_true')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    if args.live_read and args.action!='doctor':parser.error('--live-read is only available for doctor')
    try:
        state=args.state.expanduser().absolute() # Do not resolve away symlinks.
        options=InstallOptions(ROOT,state,(args.openclaw or Path(shutil.which('openclaw') or '/missing/openclaw')).absolute(),
                               (args.node or Path(shutil.which('node') or '/missing/node')).absolute(),
                               tuple(default if value is None else value for value,default in zip((args.port,args.read_port,args.proposal_port),(18080,18880,18881))))
        if state.exists():
            manifest=load_manifest(state)
            # Runtime paths and ports are durable, never silently rediscovered.
            if args.openclaw or args.node or any(x is not None for x in (args.port,args.read_port,args.proposal_port)):
                raise InstallError('immutable_options','An existing install uses its manifest paths/ports; do not override them.')
            options=InstallOptions(ROOT,state,Path(manifest['openclaw']),Path(manifest['node']),tuple(manifest['ports']))
        if args.action=='setup':
            from ohi_native.setup import setup_install
            result=setup_install(options)
            result['human_key_file']=str(state/'human.key')
        elif args.action=='start':
            from ohi_native.processes import run_install
            return run_install(options)
        else:
            from ohi_native.doctor import diagnose
            result=diagnose(options,args.live_read)
            print(json.dumps(result,indent=2))
            return 0 if result['ok'] else result.get('exit_code',1)
        print(json.dumps(result,indent=2))
        return 0
    except (InstallError,ValueError,KeyError) as error:
        code=error.code if isinstance(error,InstallError) else 'invalid_options'
        print(json.dumps({'error':code,'message':str(error) if not isinstance(error,KeyError) else 'Installation manifest is invalid.'}),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
