"""Exercise real P5 with offline inputs and save all evidence."""
import json
import socket
import time
import traceback
from pathlib import Path
from agentd.service import AgentDService

root=Path('/evidence/acceptance-20261012-r01');root.mkdir(exist_ok=True)
lookup=socket.getaddrinfo
def offline_lookup(host,*args,**kwargs):
    if 'ncbi.nlm.nih.gov' in str(host):
        raise AssertionError('Runtime attempted online NCBI access')
    return lookup(host,*args,**kwargs)
socket.getaddrinfo=offline_lookup
service=AgentDService(state_dir=root/'p5-jobs')
assert service.config['internal_control']['entrez']['enabled'] is False
start=time.monotonic()
print('P5_OFFLINE_START pig; NCBI DNS guarded',flush=True)
try:
    result=service.wf02_wf01_gateway_v2.design_p5_internal_control({'host_species':'pig','allow_fallback':False})
except Exception as exc:
    result={'status':'ERROR','error':str(exc),'traceback':traceback.format_exc()}
result['acceptance_elapsed_seconds']=round(time.monotonic()-start,2)
(root/'p5-offline-pig.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
print(json.dumps({k:result.get(k) for k in ['status','gene_selected','verdict','conservation','warnings','acceptance_elapsed_seconds','error']},ensure_ascii=False),flush=True)
