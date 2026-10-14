"""Authenticated live API smoke test; credentials and signed URLs are not logged."""
import json
import time
import urllib.request
from pathlib import Path
import jwt
import hashlib
from agentd.auth import JWTSettings
from agentd.service import AgentDService

settings=JWTSettings.from_env()
claims={'sub':'oligosmith-offline-acceptance','exp':int(time.time())+600}
if settings.issuer:claims['iss']=settings.issuer
if settings.audience:claims['aud']=settings.audience
headers={'Content-Type':'application/json'}
if settings.required:headers['Authorization']='Bearer '+jwt.encode(claims,settings.secret,algorithm=settings.algorithm)
def request(path,payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000'+path,data=data,headers=headers),timeout=120) as response:
        return response.read(),response.status,response.headers.get('Content-Type')

inputs=json.loads(Path('/tmp/acceptance-inputs.json').read_text())
checks={}
for name in ['pig','cattle','chicken','sheep','goat']:
    raw,status,_=request('/v1/wf01/tasks/preflight',{'fasta_text':inputs['single'],'batch_defaults':{'target_organism':{'name':'CSFV'},'hosts':[{'name':name}]}})
    result=json.loads(raw)
    assert not any(i['code']=='UNSUPPORTED_HOST_DATABASE' for i in result['issues'])
    checks[name]={'http_status':status,'can_auto_run':result.get('can_auto_run')}

service=AgentDService()
out=Path('/data/jobs/offline-acceptance-live-20261012.json')
if out.exists():
    bid=json.loads(out.read_text())['batch_id']
else:
    batch=service.create_wf01_v02_batch({'task_name':'Acceptance offline report transport (design only)','fasta_text':inputs['single']},interaction_mode='NO_POPUPS')
    bid=batch['batch_id']
    service.run_wf01_v02_until_blocked(bid)
report=service.get_wf01_v02_report(bid)
raw,status,_=request('/v1/wf01/tasks/'+bid+'/report')
assert json.loads(raw)['batch_id']==bid
files={}
for format_name in ['html','process_html','scientific_data']:
    try:
        path=service.get_wf01_v02_export_path(bid,format_name)
        downloaded,http_status,content_type=request('/v1/wf01/tasks/'+bid+'/exports/'+format_name)
        assert hashlib.sha256(downloaded).digest()==hashlib.sha256(path.read_bytes()).digest()
        files[format_name]={'name':path.name,'bytes':path.stat().st_size,'http_status':http_status,'content_type':content_type,'sha256':hashlib.sha256(downloaded).hexdigest()}
    except Exception as exc:
        files[format_name]={'error':str(exc)}
result={'scope':'live API preflight and design-only report transport; not full scientific acceptance','batch_id':bid,'host_preflight':checks,'report_http_status':status,'exports':files,'report_status':report.get('status')}
out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False),flush=True)
