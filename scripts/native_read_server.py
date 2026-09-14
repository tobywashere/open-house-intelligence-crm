"""Acceptance-only request observer around the actual FastAPI application."""
import json
import os
from pathlib import Path
import time

from app.main import app as backend


async def app(scope,receive,send):
    if scope['type']!='http':return await backend(scope,receive,send)
    status=None
    async def observe(message):
        nonlocal status
        if message['type']=='http.response.start':status=message['status']
        await send(message)
    await backend(scope,receive,observe)
    if scope['path'] in ['/api/leads','/api/chat/directory']:
        headers=dict(scope['headers'])
        entry={'at':time.time(),'method':scope['method'],'path':scope['path'],'status':status,
               'request_id':headers.get(b'x-openhouse-read-request',b'').decode(),
               'actor':headers.get(b'x-actor',b'user').decode()}
        with (Path(os.environ['NATIVE_READ_EVIDENCE_DIR'])/'backend-requests.jsonl').open('a') as f:f.write(json.dumps(entry)+'\n')


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ['NATIVE_READ_PORT']),log_level='warning')
