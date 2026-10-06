"""Read-only diagnostics, with one explicitly requested native read."""
from datetime import datetime,timezone
import re
import time

from .errors import InstallError
from .runtime import local_json,preflight
from .state import read_secrets,validate_install
from .processes import check_services


def receipt_total(receipt):
    # Validate the public envelope without importing the application (or its DB setup).
    if set(receipt)!={'request_id','operation','result'} or not isinstance(receipt['request_id'],str) or not re.fullmatch('[a-f0-9]{32}',receipt['request_id']) or receipt['operation']!='list_lead_directory':raise ValueError()
    result=receipt['result']
    if not isinstance(result,dict) or set(result)!={'total','offset','limit','leads'}:raise ValueError()
    total=result['total'];rows=result['leads']
    if type(total) is not int or total<0 or type(result['offset']) is not int or result['offset']!=0 or type(result['limit']) is not int or result['limit']!=25 or not isinstance(rows,list) or len(rows)!=min(total,25):raise ValueError()
    # The backend performs full lead-field validation; doctor never renders those fields.
    if any(not isinstance(row,dict) or type(row.get('id')) is not int for row in rows) or len({r['id'] for r in rows})!=len(rows):raise ValueError()
    return total


def diagnose(options,live_read=False):
    result={'ok':False,'installed':False,'configured':False,'reachable':False,'live_read':None,
            'checked_at':datetime.now(timezone.utc).isoformat(),'source_revision':None,'issues':[],'exit_code':2}
    try:
        manifest=validate_install(options);result['installed']=True
        result['source_revision']=manifest['source']['revision']
        if preflight(options)!=manifest['runtime']:
            raise InstallError('runtime_changed','Restore the documented runtime and model.')
        result['configured']=True;result['exit_code']=1
        keys=read_secrets(options.state);check_services(options,keys)
        result['reachable']=True
        if live_read:
            start=time.monotonic()
            receipt=local_json(f'http://127.0.0.1:{options.ports[0]}/api/chat/directory',token=keys['human'],
                               body={'message':'How many leads are there?'},timeout=100)
            try:total=receipt_total(receipt)
            except (ValueError,TypeError,KeyError):
                raise InstallError('invalid_receipt','Native read did not return a validated receipt; no retry was made.') from None
            result['live_read']={'verified':True,'total':total,'seconds':round(time.monotonic()-start,3)}
        result['ok']=True;result['exit_code']=0
    except InstallError as error:
        result['issues'].append({'code':error.code,'message':str(error)})
    return result
