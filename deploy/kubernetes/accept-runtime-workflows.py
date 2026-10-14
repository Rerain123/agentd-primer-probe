"""Real-tool acceptance; results record scientific failures without relabeling them."""
import io
import json
import os
import time
import traceback
from pathlib import Path
from Bio import SeqIO
from agentd.service import AgentDService

root=Path('/evidence/acceptance-20261012-r02')
root.mkdir(parents=True,exist_ok=True)
service=AgentDService(state_dir=root/'jobs')
inputs=json.loads(Path('/scripts/acceptance-inputs.json').read_text())
results={}


def save(name,result,start):
    results[name]={'elapsed_seconds':round(time.monotonic()-start,2),'result':result}
    (root/(name+'.json')).write_text(json.dumps(results[name],ensure_ascii=False,indent=2,default=str))
    print(json.dumps({'case':name,'elapsed_seconds':results[name]['elapsed_seconds'],'status':result.get('status'),'batch_id':result.get('batch_id'),'error':result.get('error')},ensure_ascii=False),flush=True)


def wf01(name,fasta,full=False):
    start=time.monotonic()
    payload={'task_name':'Acceptance '+name,'fasta_text':fasta}
    if full:
        payload['batch_defaults']={'target_organism':{'name':'CSFV','accession':'NC_002657.1'},'hosts':[{'name':'pig'}]}
    try:
        batch=service.create_wf01_v02_batch(payload,interaction_mode='NO_POPUPS',require_full_route=full)
        print(json.dumps({'case':name,'event':'CREATED','batch_id':batch['batch_id']}),flush=True)
        state=service.run_wf01_v02_until_blocked(batch['batch_id'])
        report=service.get_wf01_v02_report(batch['batch_id'])
        save(name,{'status':state.get('status'),'batch_id':batch['batch_id'],'state':state,'report':report},start)
    except Exception as exc:
        save(name,{'status':'ERROR','error':str(exc),'traceback':traceback.format_exc()},start)


print('ACCEPTANCE_START',flush=True)
start=time.monotonic()
assert service.config['internal_control']['entrez']['enabled'] is False
assert service.workflow_runtime_contracts['WF-02']['embedded_dependency']=='wf02-wf01-execution-snapshot-v3'
ready=service.catalog.status()
save('resources',{'status':'CHECKED','catalog':ready,'p5':service.wf02_wf01_gateway_v3.internal_designer(service.config,service.wf02_wf01_catalog_v3).status()},start)
wf01('wf01-single-design',inputs['single'])
wf01('wf01-multiple-template-design',inputs['multiple'])
wf01('wf01-single-full-nt',inputs['single'],True)
start=time.monotonic()
try:
    payload={'task_name':'Acceptance two targets with offline IC','host_species':'pig','include_internal_control':True,'targets':inputs['wf02_targets']}
    created=service.create_wf02_v01_batch(payload)
    batch=created.get('batch') or created
    batch_id=batch['batch_id']
    print(json.dumps({'case':'wf02-two-target-internal','event':'CREATED','batch_id':batch_id}),flush=True)
    result=service.run_wf02_v01_until_blocked(batch_id)
    persisted=service.get_wf02_v01_batch(batch_id)
    save('wf02-two-target-internal',{'status':persisted.get('status'),'batch_id':batch_id,'state':result,'batch':persisted},start)
    # A second service instance checks persisted resume routing and locks.
    restarted=AgentDService(state_dir=root/'jobs')
    resumed=restarted.get_wf02_v01_batch(batch_id)
    binding=restarted.wf02_v01._require_gateway(resumed).SNAPSHOT_VERSION
    save('wf02-reload',{'status':'VERIFIED','batch_id':batch_id,'snapshot_version':binding,'persisted_status':resumed.get('status')},time.monotonic())
except Exception as exc:
    save('wf02-two-target-internal',{'status':'ERROR','error':str(exc),'traceback':traceback.format_exc()},start)
(root/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
print('ACCEPTANCE_FINISHED; inspect scientific states before acceptance',flush=True)
