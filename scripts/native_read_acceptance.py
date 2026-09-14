#!/usr/bin/env python3
"""Local-only synthetic acceptance harness, not a production installer.

Run with the project's Python environment and OpenClaw/Ollama on PATH.
Precondition: Ollama already serves the pinned local Qwen model on 11434.
This creates a separate profile, never modifies the active OpenClaw profile.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','start','stop','snapshot'])
    parser.add_argument('--profile', default='ohi-dashboard-read')
    parser.add_argument('--count', type=int, default=37)
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--gateway-port', type=int, default=18879)
    args = parser.parse_args()
    if not args.profile.startswith('ohi-') or not args.profile.replace('-','').isalnum():
        parser.error('Use an isolated ohi- profile name')
    state = Path.home()/('.openclaw-'+args.profile)
    if args.action == 'prepare':
        if not 0 <= args.count <= 1000: parser.error('count must be 0–1000')
        state.mkdir(mode=0o700,exist_ok=False)
        ws=state/'workspace';ws.mkdir()
        (ws/'AGENTS.md').write_text('Use the CRM tool for lead counts and directory reads. Report only tool facts. This agent cannot change records.\n')
        token=secrets.token_hex(32)
        config={
          'models':{'mode':'replace','providers':{'ollama':{'api':'ollama','apiKey':'local-ollama','baseUrl':'http://127.0.0.1:11434','models':[
            {'id':'qwen3.5:9b','name':'qwen3.5:9b','reasoning':True,'input':['text','image'],
             'contextWindow':16384,'maxTokens':2048,'params':{'num_ctx':16384},
             'compat':{'supportsTools':True,'supportsUsageInStreaming':True,'supportsJsonSchemaResponseFormat':True}}]}}},
          'agents':{'defaults':{'workspace':str(ws),'skipBootstrap':True,'model':{'primary':'ollama/qwen3.5:9b','fallbacks':[]}},'entries':{
            'native-read':{'workspace':str(ws),'agentDir':str(state/'agents/native-read/agent'),'skills':[],'thinkingDefault':'off',
              'experimental':{'localModelLean':False},'tools':{'profile':'full','allow':['openhouse_crm']}}}},
          'tools':{'profile':'full','allow':['openhouse_crm']},
          'plugins':{'allow':['ollama','openhouse-read'],'load':{'paths':[str(ROOT/'openclaw-plugins/openhouse-read')]},
            'entries':{'ollama':{'enabled':True},'openhouse-read':{'enabled':True,'config':{'agentId':'native-read','crmApiUrl':f'http://127.0.0.1:{args.port}/api'}}},'slots':{'memory':'none'}},
          'gateway':{'mode':'local','bind':'loopback','port':args.gateway_port,'auth':{'mode':'token','token':token},'http':{'endpoints':{'chatCompletions':{'enabled':True}}}},
          'logging':{'level':'debug','file':str(state/'gateway.log')},
        }
        (state/'openclaw.json').write_text(json.dumps(config,indent=2)+'\n');(state/'openclaw.json').chmod(0o600)
        (state/'fixture.json').write_text(json.dumps({'port':args.port,'gateway_port':args.gateway_port,'count':args.count}))
        os.environ['DB_PATH']=str(state/'fixture.db');sys.path.insert(0,str(ROOT/'backend'))
        from app.db import init_db
        init_db()
        with sqlite3.connect(state/'fixture.db') as conn:
            for i in range(args.count):conn.execute('insert into leads(name,status,source) values (?,?,?)',(f'Synthetic Lead {i+1:02}','new','acceptance'))
        snapshot(state,'before')
        print('Prepared isolated fixture:',state)
        return
    if args.action=='snapshot': snapshot(state,'after');return
    if args.action=='stop':
        pids=json.loads((state/'pids.json').read_text())
        for name,pid in pids.items():
            proc=Path(f'/proc/{pid}/cmdline')
            if not proc.exists():continue
            command=proc.read_bytes().decode().replace('\0',' ')
            expected='openclaw' if name=='gateway' else 'native_read_server.py'
            if expected not in command or os.getpgid(pid)!=pid:raise RuntimeError('PID ownership changed; refusing to stop')
            os.killpg(pid,signal.SIGTERM)
        print('Stopped isolated gateway and backend');return
    settings=json.loads((state/'fixture.json').read_text())
    config=json.loads((state/'openclaw.json').read_text())
    # No inherited provider keys, channel credentials, or live integration env.
    env={k:os.environ[k] for k in ['HOME','USER','PATH','LANG'] if k in os.environ}
    env.update(DB_PATH=str(state/'fixture.db'),AGENT_MODE='mock',INTEGRATIONS_MODE='off',INTEGRATIONS_POLLER='off',
               NATIVE_READ_GATEWAY_URL=f'http://127.0.0.1:{settings["gateway_port"]}',NATIVE_READ_GATEWAY_TOKEN=config['gateway']['auth']['token'],
               PYTHONPATH=str(ROOT/'backend'),NATIVE_READ_EVIDENCE_DIR=str(state),NATIVE_READ_PORT=str(settings['port']))
    commands={'backend':[sys.executable,str(ROOT/'scripts/native_read_server.py')],
              'gateway':[shutil.which('openclaw') or 'openclaw','--profile',args.profile,'gateway','run','--port',str(settings['gateway_port']),'--bind','loopback']}
    pids={}
    for name,command in commands.items():
        with (state/(name+'-stdout.log')).open('w') as log:
            process=subprocess.Popen(command,env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        pids[name]=process.pid
        (state/'pids.json').write_text(json.dumps(pids))
    print('Started isolated processes:',pids)


def snapshot(state,label):
    with sqlite3.connect('file:'+str(state/'fixture.db')+'?mode=ro',uri=True) as conn:
        tables=['leads','events','pending_changes','appointments','reminders']
        data={t:[list(row) for row in conn.execute('select * from '+t+' order by id')] for t in tables}
    value={'counts':{t:len(rows) for t,rows in data.items()},'crm_content_sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()}
    (state/(label+'.json')).write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))


if __name__=='__main__':main()
