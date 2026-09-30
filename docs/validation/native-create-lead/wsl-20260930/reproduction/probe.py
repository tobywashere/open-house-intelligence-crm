import json,pathlib,sqlite3,sys,hashlib
import httpx
out=pathlib.Path('/mnt/c/Users/ankus/OneDrive/Documents/GitHub/open-house-intelligence-crm/wsl-evidence/native-create-lead')
state=pathlib.Path.home()/'.ohi-native-acceptance/ohi-native-proposal-review'
runtime=next(state.rglob('openclaw-agent.sqlite'))
ids=json.loads((out/'proposal-live-20260930/ids.json').read_text())
def inference():
    with sqlite3.connect('file:'+str(runtime)+'?mode=ro',uri=True) as conn:
        messages=[json.loads(row[0]).get('message',{}) for row in conn.execute('select event_json from transcript_events order by session_id,seq')]
        assistants=[m for m in messages if m.get('role')=='assistant']
        calls=[b for m in assistants for b in m.get('content',[]) if b.get('type')=='toolCall']
        return {'assistant_messages':len(assistants),'native_tool_calls':len(calls),'transcript_sha256':hashlib.sha256(json.dumps(messages,sort_keys=True).encode()).hexdigest()}
before=inference()
with httpx.Client(base_url='http://127.0.0.1:18082/api',trust_env=False,follow_redirects=False,timeout=70,headers={'X-API-Token':(state/'human.key').read_text().strip()}) as client:
    original=client.get('/chat/lead-proposal/'+ids['approved']);original.raise_for_status();record=original.json();assert record['state']=='approved'
    replay=client.post('/chat/lead-proposal',json={'request_id':ids['approved'],'message':'Propose Synthetic WSL Person, synthetic@example.invalid, 555-0100'})
    assert replay.status_code==200 and replay.json()==record
    duplicate=client.post('/pending-changes/'+str(record['proposal']['id'])+'/approve',json={})
    assert duplicate.status_code==400
    denied=client.get('/chat/lead-proposal/'+ids['denied']);assert denied.json()['state']=='denied'
    leads=client.get('/leads').json();assert len(leads)==1 and leads[0]['name']=='Human Edited WSL Person' and leads[0]['email']=='edited@example.invalid'
after=inference();assert before==after,'Replay/status/duplicate approval changed inference transcript'
data={'phase':sys.argv[1],'passed':True,'request_id':ids['approved'],'replay_status':replay.status_code,'replay_identical':True,'duplicate_approval_status':duplicate.status_code,'denied_state':denied.json()['state'],'lead_count':len(leads),'approved_lead_id':record['proposal']['result']['id'],'inference_before':before,'inference_after':after,'no_additional_inference':before==after}
(out/('probe-'+sys.argv[1]+'.json')).write_text(json.dumps(data,indent=2)+'\n');print(json.dumps(data))
