import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {randomBytes} from 'node:crypto';
import {definition, parameters, postProposal} from './proposals.js';

const id = 'a'.repeat(32);
const agentId = 'native-proposals';
const context = {agentId, sessionKey:`agent:${agentId}:openai-user:ohi-propose-${id}`};
const receipt = {request_id:id, state:'proposed', proposal:{id:1, operation:'create_lead', status:'pending', payload:{name:'Synthetic', source:'note', raw_text:'Synthetic request'}, result:null}};
function setup({ctx=context, env={OHI_AGENT_API_TOKEN:randomBytes(32).toString('hex')}, result=receipt, postImpl, config={agentId, crmApiUrl:'http://127.0.0.1:8000/api'}}={}) {
  let factory; const calls=[];
  definition({env, postImpl:postImpl ?? (async (...args)=>{calls.push(args);return result;})}).register({pluginConfig:config, registerTool(fn, options){factory=fn;assert.deepEqual(options,{name:'openhouse_propose_lead'});}});
  return {tool:factory(ctx),calls};
}

test('simple fixed schema and request context supplies identity', async()=>{
  assert.deepEqual(Object.keys(parameters.properties), ['name','email','phone']);
  assert.deepEqual(parameters.required,['name']);
  const {tool,calls}=setup();
  assert.equal(tool.name,'openhouse_propose_lead');
  const output=await tool.execute('call',{name:'  Synthetic  ', email:' synthetic@example.test '});
  assert.deepEqual(output.details,receipt);
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'http://127.0.0.1:8000/api/agent/lead-proposals');
  assert.deepEqual(calls[0][2],{request_id:id,name:'Synthetic',email:'synthetic@example.test'});
});

test('duplicate calls submit the same durable ID',async()=>{
  const {tool,calls}=setup();
  assert.deepEqual(await tool.execute('1',{name:'Synthetic'}), await tool.execute('2',{name:'Synthetic'}));
  assert.equal(calls[0][2].request_id,calls[1][2].request_id);
});

for(const ctx of [{...context,agentId:'other'}, {...context,sessionKey:context.sessionKey+'\n'}, {...context,sessionKey:context.sessionKey+':suffix'}, {...context,sessionKey:context.sessionKey.replace(id,id.toUpperCase())}, {...context,sessionKey:'ohi-propose-'+id}, {}]) {
  test('wrong runtime identity fails: '+JSON.stringify(ctx),async()=>{
    const {tool,calls}=setup({ctx});
    await assert.rejects(tool.execute('x',{name:'Synthetic'}),/current proposal request/);
    assert.equal(calls.length,0);
  });
}
for(const args of [null, [], {}, {name:''}, {name:' '}, {name:1}, {name:'a'.repeat(201)}, {name:'A',email:null}, {name:'A',phone:1}, {name:'A',email:'a'.repeat(321)}, {name:'A',phone:'a'.repeat(101)}, {name:'A',actor:'human'}, {name:'A',request_id:id}, {name:'A',url:'http://evil'}, {name:'A',operation:'delete_lead'}]) {
  test('invalid arguments fail: '+JSON.stringify(args),async()=>{
    const {tool,calls}=setup();
    await assert.rejects(tool.execute('x',args),/Invalid proposal fields/);
    assert.equal(calls.length,0);
  });
}
for(const env of [{}, {OHI_AGENT_API_TOKEN:'short'}, {OHI_AGENT_API_TOKEN:'a'.repeat(32),OHI_API_TOKEN:''}, {OHI_AGENT_API_TOKEN:'a'.repeat(32),OHI_API_TOKEN:'human-secret'}]) {
  test('registration refuses unsafe credentials '+Object.keys(env).join(','),()=>assert.throws(()=>setup({env}),/credential/));
}
for(const config of [{agentId,crmApiUrl:'https://127.0.0.1/api'}, {agentId,crmApiUrl:'http://example.com/api'}, {agentId,crmApiUrl:'http://user:pass@localhost/api'}, {agentId,crmApiUrl:'http://localhost/api?q=x'}, {agentId:'native-proposals\n',crmApiUrl:'http://localhost/api'}]) {
  test('registration rejects unsafe config '+JSON.stringify(config),()=>assert.throws(()=>setup({config}),/configuration/));
}
for(const result of [{}, {...receipt,request_id:'b'.repeat(32)}, {...receipt,state:'approved'}, {...receipt,proposal:{...receipt.proposal,id:'1'}}, {...receipt,proposal:{...receipt.proposal,operation:'delete_lead'}}]) {
  test('invalid verified result fails '+JSON.stringify(result),async()=>{
    const {tool}=setup({result});
    await assert.rejects(tool.execute('x',{name:'Synthetic'}),/^Error: CRM proposal failed$/);
  });
}

test('provider errors are sanitized',async()=>{
  const {tool}=setup({postImpl:async()=>{throw Error('private credential and prompt');}});
  await assert.rejects(tool.execute('x',{name:'Synthetic'}),/^Error: CRM proposal failed$/);
});

async function serverTest(handler, run) {
  const server=http.createServer(handler);
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {await run(`http://127.0.0.1:${server.address().port}/api/agent/lead-proposals`);}
  finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}

test('real HTTP transport sends fixed POST and ignores ambient proxies',async()=>{
  const old=process.env.HTTP_PROXY;process.env.HTTP_PROXY='http://127.0.0.1:1';
  try {await serverTest((req,res)=>{
    assert.equal(req.method,'POST');assert.equal(req.url,'/api/agent/lead-proposals');
    assert.equal(req.headers['x-api-token'],'test-agent-token');
    assert.equal(req.headers['x-actor'],undefined);
    res.end(JSON.stringify(receipt));
  },async url=>assert.deepEqual(await postProposal(url,'test-agent-token',{name:'Synthetic'}),receipt));}
  finally {if(old===undefined)delete process.env.HTTP_PROXY;else process.env.HTTP_PROXY=old;}
});

test('real HTTP transport rejects redirect, oversized body and timeout',async()=>{
  await serverTest((req,res)=>{res.writeHead(302,{Location:'http://127.0.0.1:1'});res.end();},async url=>assert.rejects(postProposal(url,'token',{})));
  await serverTest((req,res)=>res.end('a'.repeat(65537)),async url=>assert.rejects(postProposal(url,'token',{})));
  await serverTest(()=>{},async url=>assert.rejects(postProposal(url,'token',{}, {timeoutMs:25})));
});

for(const suffix of ['\n','\r','\u2028','\u2029']) {
  test('registration rejects exact credential and agent suffix '+JSON.stringify(suffix),()=>{
    assert.throws(()=>setup({env:{OHI_AGENT_API_TOKEN:'a'.repeat(32)+suffix}}),/credential/);
    assert.throws(()=>setup({config:{agentId:agentId+suffix,crmApiUrl:'http://localhost/api'}}),/configuration/);
  });
}

test('response validation accepts backend character limits for Unicode messages',async()=>{
  const result={...receipt,proposal:{...receipt.proposal,payload:{...receipt.proposal.payload,raw_text:'😀'.repeat(2000)}}};
  const {tool}=setup({result});
  assert.deepEqual((await tool.execute('x',{name:'Synthetic'})).details,result);
});
