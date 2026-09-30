import hashlib,json,pathlib,sqlite3,statistics,urllib.request
out=pathlib.Path('/mnt/c/Users/ankus/OneDrive/Documents/GitHub/open-house-intelligence-crm/wsl-evidence/native-create-lead')
read=json.loads((out/'read-live-20260930/results.json').read_text())
ids=json.loads((out/'proposal-live-20260930/ids.json').read_text())
all_traces={}
for kind,profile,agent,prefix,rids in [('read','ohi-native-read-review','native-read','ohi-read-',[c['response']['request_id'] for c in read['cases'][:10]]),('proposals','ohi-native-proposal-review','native-proposals','ohi-propose-',list(ids.values()))]:
    state=pathlib.Path.home()/'.ohi-native-acceptance'/profile
    runtime=next(state.rglob('openclaw-agent.sqlite'))
    traces=[]
    with sqlite3.connect('file:'+str(runtime)+'?mode=ro',uri=True) as conn:
        for rid in rids:
            session=conn.execute('select session_id from session_windows where session_key=?',('agent:'+agent+':openai-user:'+prefix+rid,)).fetchone()[0]
            messages=[json.loads(row[0]).get('message',{}) for row in conn.execute('select event_json from transcript_events where session_id=? order by seq',(session,))]
            assistants=[m for m in messages if m.get('role')=='assistant']
            calls=[b for m in assistants for b in m.get('content',[]) if b.get('type')=='toolCall']
            results=[m for m in messages if m.get('role')=='toolResult']
            starts=[];exposed=[]
            for (raw,) in conn.execute('select event_json from trajectory_runtime_events where session_id=? order by seq',(session,)):
                event=json.loads(raw)
                if event.get('type')=='session.started':
                    d=event['data'];starts.append({'native_tool_count':d.get('toolCount'),'client_tool_count':d.get('clientToolCount')})
                if event.get('type')=='context.compiled':exposed=[t['name'] for t in event['data'].get('tools',[])]
            trace={'request_id':rid,'agent':agent,'session_id':session,'internal_inference_rounds':len(assistants),'providers':sorted({m.get('provider') for m in assistants}),'models':sorted({m.get('model') for m in assistants}),'starts':starts,'exposed_tool_names':exposed,'calls':[{'name':c['name'],'arguments':c['arguments']} for c in calls],'tool_results':[]}
            for m in results:
                for b in m.get('content',[]):
                    if b.get('type')=='text':
                        try:trace['tool_results'].append(json.loads(b['text']))
                        except ValueError:trace['tool_results'].append({'non_json_result':True})
            assert trace['providers']==['ollama'] and trace['models']==['qwen3.5:9b']
            expected='openhouse_crm' if kind=='read' else 'openhouse_propose_lead'
            assert len(calls)==1 and calls[0]['name']==expected
            assert exposed==[expected] and starts==[{'native_tool_count':1,'client_tool_count':0}]
            if kind=='read':assert trace['tool_results']==[next(c['response']['result'] for c in read['cases'] if c['response'].get('request_id')==rid)]
            traces.append(trace)
    all_traces[kind]=traces
(out/'native-traces.json').write_text(json.dumps(all_traces,indent=2)+'\n')
runtime={'openclaw':'2026.8.1-beta.3 (5831b80)','ollama_client':'0.32.15','node':'v24.15.0','python':'3.14.4','playwright':'1.62.1','edge':read['browser_version']}
for key,endpoint in [('ollama_server','version'),('loaded_model','ps')]:
    with urllib.request.urlopen('http://127.0.0.1:11434/api/'+endpoint) as response:runtime[key]=json.load(response)
runtime['configured']={'context':16384,'maxTokens':2048,'thinking':'off','localModelLean':False,'fallbacks':[],'provider':'ollama','model':'qwen3.5:9b'}
(out/'runtime.json').write_text(json.dumps(runtime,indent=2)+'\n')
snapshots={}
for profile in ['ohi-native-read-review','ohi-native-proposal-review']:
    db=pathlib.Path.home()/'.ohi-native-acceptance'/profile/'fixture.db'
    with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as conn:
        tables=['leads','events','pending_changes','appointments','reminders','native_lead_requests']
        data={t:conn.execute('select * from '+t+' order by rowid').fetchall() for t in tables}
        snapshots[profile]={'counts':{k:len(v) for k,v in data.items()},'content_sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),'pending_by_status':dict(conn.execute('select status,count(*) from pending_changes group by status').fetchall())}
(out/'final-fixture-snapshots.json').write_text(json.dumps(snapshots,indent=2)+'\n')
summary={'read_cases':len(read['cases']),'read_passed':sum(c['passed'] for c in read['cases']),'read_median_ms':statistics.median(c['latency_ms'] for c in read['cases'][:10]),'read_trace_count':len(all_traces['read']),'proposal_trace_count':len(all_traces['proposals']),'final_snapshots':snapshots}
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
