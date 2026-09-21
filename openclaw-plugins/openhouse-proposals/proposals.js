import http from 'node:http';

const MAX_BYTES = 65_536;
const ID = /^[a-f0-9]{32}$/;
const exactId = value => typeof value === 'string' && value.length === 32 && ID.test(value);
const exactAgent = value => typeof value === 'string' && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value) && !value.includes('\n');
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const parameters = {
  type:'object', additionalProperties:false, required:['name'],
  properties:{name:{type:'string',minLength:1,maxLength:200}, email:{type:'string',maxLength:320}, phone:{type:'string',maxLength:100}},
};

function normalize(args) {
  if (!record(args) || !Object.hasOwn(args,'name') || Object.keys(args).some(k=>!['name','email','phone'].includes(k))) throw Error('Invalid proposal fields');
  const fields = {};
  for (const [key,max] of [['name',200],['email',320],['phone',100]]) {
    if (!Object.hasOwn(args,key)) continue;
    const value=args[key];
    if(typeof value!=='string' || Array.from(value).length>max || (key==='name'&&!value.trim())) throw Error('Invalid proposal fields');
    fields[key]=value.trim();
  }
  return fields;
}

function verify(value,id) {
  if (!record(value) || !exactId(value.request_id) || value.request_id!==id || !record(value.proposal)) throw Error();
  const p=value.proposal;
  if (!Number.isSafeInteger(p.id) || p.id<1 || p.operation!=='create_lead' || !record(p.payload)
      || p.payload.source!=='note' || typeof p.payload.raw_text!=='string' || Array.from(p.payload.raw_text).length>2000) throw Error();
  normalize(Object.fromEntries(Object.entries(p.payload).filter(([key])=>['name','email','phone'].includes(key))));
  const state = {pending:'proposed', approved:'approved', denied:'denied'}[p.status];
  if (!state || value.state!==state) throw Error();
  if (state==='approved' && (!record(p.result) || !Number.isSafeInteger(p.result.id) || p.result.id<1)) throw Error();
  return value;
}

export function postProposal(url, token, payload, {timeoutMs=10_000}={}) {
  // node:http uses a direct socket, never ambient HTTP(S)_PROXY settings.
  // A single absolute timer covers connection, headers, and body delivery.
  return new Promise((resolve,reject)=>{
    let timer;
    const request=http.request(url, {method:'POST',headers:{'Content-Type':'application/json','X-API-Token':token}}, response=>{
      if(response.statusCode!==200) {response.resume();request.destroy();reject(Error('CRM backend unavailable'));return;}
      const chunks=[];let bytes=0;
      response.on('data',chunk=>{
        bytes+=chunk.length;
        if(bytes>MAX_BYTES){request.destroy(Error('CRM response too large'));return;}
        chunks.push(chunk);
      });
      response.on('error',reject);
      response.on('end',()=>{
        try {resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));} catch {reject(Error('Invalid CRM response'));}
      });
    });
    request.on('error',reject);
    request.on('close',()=>clearTimeout(timer));
    timer=setTimeout(()=>request.destroy(Error('CRM proposal timed out')),timeoutMs);
    request.end(JSON.stringify(payload));
  });
}

export function definition({postImpl=postProposal,env=process.env}={}) {
  return {
    id:'openhouse-proposals', name:'OpenHouse lead proposals',
    register(api) {
      const token=env.OHI_AGENT_API_TOKEN;
      if(Object.hasOwn(env,'OHI_API_TOKEN') || typeof token!=='string' || token.length<32 || !/^[\x21-\x7e]+$/.test(token)) throw Error('Proposal plugin requires only an agent credential');
      const {agentId,crmApiUrl}=api.pluginConfig;
      let base;
      try {
        base=new URL(crmApiUrl);
        if(!exactAgent(agentId) || base.protocol!=='http:' || !['localhost','127.0.0.1','[::1]'].includes(base.hostname)
          || base.username || base.password || base.search || base.hash || base.pathname.replace(/\/$/,'')!=='/api') throw Error();
      } catch {throw Error('Invalid local proposal configuration');}
      const endpoint=base.href.replace(/\/$/,'')+'/agent/lead-proposals';
      api.registerTool(context=>({
        name:'openhouse_propose_lead',
        description:'Propose a new CRM lead for human review. This queues name and optional contact fields; it does not create or approve a lead.',
        parameters,
        async execute(_callId,args) {
          const prefix=`agent:${agentId}:openai-user:ohi-propose-`;
          const key=context.sessionKey;
          const id=typeof key==='string' && key.startsWith(prefix) ? key.slice(prefix.length) : '';
          if(context.agentId!==agentId || !exactId(id) || key!==prefix+id) throw Error('No current proposal request');
          const fields=normalize(args);
          try {
            const result=verify(await postImpl(endpoint,token,{request_id:id,...fields}),id);
            return {content:[{type:'text',text:JSON.stringify(result)}],details:result};
          } catch {throw Error('CRM proposal failed');}
        },
      }),{name:'openhouse_propose_lead'});
    },
  };
}
