#!/usr/bin/env python3
"""Acceptance-only: hold one authenticated tool submission before forwarding once.

Never imported by the application or native launcher. Use disposable CRM state.
"""
import argparse
from datetime import datetime,timezone
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ohi_native.errors import InstallError
from ohi_native.runtime import local_opener
from ohi_native.state import atomic_json,private_dir,read_private

ROUTE='/api/agent/lead-proposals'


def make_server(target,key_file,control,request_id,port,hold_seconds=180):
    try:
        parts=urllib.parse.urlsplit(target)
        valid=(parts.scheme=='http' and parts.hostname=='127.0.0.1' and parts.port and parts.path==ROUTE
               and not parts.username and not parts.password and not parts.query and not parts.fragment)
    except ValueError:valid=False
    if not valid or not re.fullmatch('[a-f0-9]{32}',request_id) or not 0<hold_seconds<=180:
        raise InstallError('invalid_relay','Use a fixed loopback proposal endpoint, exact request ID and bounded hold.')
    key=read_private(key_file).strip()
    if not re.fullmatch('[a-f0-9]{64}',key):raise InstallError('invalid_key','Use the disposable installation agent key file.')
    private_dir(control)
    if list(control.iterdir()):raise InstallError('relay_used','Use a new empty private relay control directory for every case.')
    claimed=threading.Lock();cancel=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def reply(self,status,body=b'{}'):
            try:
                self.send_response(status);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        def do_POST(self):
            if self.path!=ROUTE:self.reply(404);return
            if not hmac.compare_digest(self.headers.get('X-API-Token',''),key):self.reply(401);return
            if self.headers.get('Transfer-Encoding') is not None:self.reply(400);return
            try:length=int(self.headers.get('Content-Length','0'))
            except ValueError:self.reply(400);return
            if not 0<length<=4096:self.reply(413);return
            try:
                body=self.rfile.read(length);value=json.loads(body)
                if not isinstance(value,dict) or value.get('request_id')!=request_id:raise ValueError()
            except (ValueError,OSError):self.reply(400);return
            if not claimed.acquire(blocking=False):self.reply(409);return
            # Lock is deliberately never released: one captured call per process.
            start=time.monotonic()
            atomic_json(control/'captured.json',{'request_id':request_id,'captured_at':datetime.now(timezone.utc).isoformat()})
            status=504;forwarded=False;response_body=b'{}'
            while not cancel.is_set() and time.monotonic()-start<hold_seconds:
                if (control/'release').exists():
                    try:read_private(control/'release')
                    except InstallError:status=400;break
                    if cancel.is_set():break
                    forwarded=True
                    request=urllib.request.Request(target,data=body,headers={'X-API-Token':key,'Content-Type':'application/json'})
                    try:
                        with local_opener().open(request,timeout=5) as response:
                            status=response.status;response_body=response.read(1_048_577)
                    except urllib.error.HTTPError as error:
                        # Redirects are rejected by local_opener; never visit their target.
                        status=error.code;response_body=b'{}'
                    except OSError:status=502
                    if len(response_body)>1_048_576:status=502;response_body=b'{}'
                    break
                cancel.wait(.025)
            atomic_json(control/'result.json',{'request_id':request_id,'status':status,'forwarded':forwarded,
                                              'seconds':round(time.monotonic()-start,3)})
            self.reply(status,response_body)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True;server.cancel=cancel
    return server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True)
    parser.add_argument('--key-file',type=Path,required=True)
    parser.add_argument('--control',type=Path,required=True)
    parser.add_argument('--request-id',required=True)
    parser.add_argument('--port',type=int,default=18882)
    args=parser.parse_args()
    try:
        server=make_server(args.target,args.key_file.absolute(),args.control.absolute(),args.request_id,args.port)
        server.timeout=.2
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:server.cancel.set())
        print(json.dumps({'acceptance_only':True,'port':server.server_port,'request_id':args.request_id}),flush=True)
        try:
            while not server.cancel.is_set():server.handle_request()
        finally:server.cancel.set();server.server_close()
        return 0
    except (InstallError,OSError,ValueError):
        print(json.dumps({'error':'relay_configuration','message':'Check the fixed endpoint, private paths, unused port and new control directory.'}))
        return 2


if __name__=='__main__':raise SystemExit(main())
