import test from 'node:test';
import assert from 'node:assert/strict';
import {definition} from './read.js';

function setup({total=37,fail=false}={}) {
  let route, factory, reads=0, time=1000;
  const api={pluginConfig:{agentId:'native-read',crmApiUrl:'http://127.0.0.1:18080/api'},
    registerHttpRoute:r=>route=r, registerTool:f=>factory=f, on:()=>{}};
  definition({now:()=>time,fetchImpl:async(url,options)=>{
    reads++; assert.equal(url,'http://127.0.0.1:18080/api/leads');assert.equal(options.method,'GET');
    if(fail)throw new Error('private backend information');
    return new Response(JSON.stringify(Array.from({length:total},(_,i)=>({id:i+1,name:`Synthetic ${i+1}`,status:'new',email:'not exported'}))));
  }}).register(api);
  const id='a'.repeat(32);
  const tool=(agentId='native-read',sessionKey=`agent:native-read:openai-user:ohi-read-${id}`)=>factory({agentId,sessionKey});
  const http=async(method,key=id)=>{
    const res={statusCode:0,setHeader(){},end(s){this.body=JSON.parse(s)}};
    await route.handler({method,url:'/openhouse/read-receipts/'+key},res); return res;
  };
  return {id,tool,http,route,reads:()=>reads,expire:()=>time+=91_000};
}
const args={operation:'list_lead_directory',arguments:{}};
test('receipt requires real execution, current session, and is single-use',async()=>{
  const s=setup();assert.equal(s.route.auth,'gateway');assert.equal((await s.http('POST')).statusCode,201);
  assert.equal((await s.http('POST')).statusCode,409);
  await assert.rejects(s.tool('wrong').execute('1',args));assert.equal(s.reads(),0);
  await assert.rejects(s.tool('native-read','old-session').execute('1',args));assert.equal(s.reads(),0);
  await s.tool().execute('2',args);assert.equal(s.reads(),1);
  await assert.rejects(s.tool().execute('3',args));assert.equal(s.reads(),1);
  const result=await s.http('GET');assert.equal(result.statusCode,200);
  assert.equal(result.body.result.total,37);assert.equal(result.body.result.leads.length,25);
  assert.deepEqual(Object.keys(result.body.result.leads[0]),['id','name','status']);
  assert.equal((await s.http('GET')).statusCode,404);
});
test('empty, failed, expired and cancelled requests stay distinct',async()=>{
  const e=setup({total:0});await e.http('POST');await e.tool().execute('1',args);
  assert.equal((await e.http('GET')).body.result.total,0);
  const f=setup({fail:true});await f.http('POST');await assert.rejects(f.tool().execute('1',args),/CRM read failed/);
  assert.equal((await f.http('GET')).statusCode,404);
  const x=setup();await x.http('POST');x.expire();await assert.rejects(x.tool().execute('1',args));assert.equal(x.reads(),0);
  const c=setup();await c.http('POST');await c.http('DELETE');await assert.rejects(c.tool().execute('1',args));
});
test('write or model-controlled URL arguments never reach backend',async()=>{
  const s=setup();await s.http('POST');
  for(const input of [{operation:'delete_lead',arguments:{}},{operation:'list_lead_directory',arguments:{url:'http://example.com'}},{operation:'list_lead_directory',arguments:'{}'}])
    await assert.rejects(s.tool().execute('1',input));
  assert.equal(s.reads(),0);
});
test('a late tool completion cannot resurrect a cancelled request',async()=>{
 let route,factory,release;
 definition({fetchImpl:async()=>{await new Promise(r=>release=r);return new Response('[]')}}).register({
  pluginConfig:{agentId:'native-read',crmApiUrl:'http://127.0.0.1:18080/api'},
  registerHttpRoute:r=>route=r,registerTool:f=>factory=f,on:()=>{},
 });
 const id='b'.repeat(32),url='/openhouse/read-receipts/'+id;
 async function http(method){const res={setHeader(){},end(){}};await route.handler({method,url},res);return res.statusCode}
 await http('POST');
 const running=factory({agentId:'native-read',sessionKey:`agent:native-read:openai-user:ohi-read-${id}`}).execute('1',args);
 await http('DELETE');release();await assert.rejects(running,/CRM read failed/);
 assert.equal(await http('GET'),404);
});
