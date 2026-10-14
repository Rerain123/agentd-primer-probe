"""Verify an existing completed design-only task is idempotent after service reload."""
import json
import hashlib
from pathlib import Path
from agentd.service import AgentDService

root = Path('/data/jobs')
bid = json.loads((root / 'offline-acceptance-live-20261012.json').read_text())['batch_id']
service = AgentDService()
before = service.get_wf01_v02_batch(bid)
service.run_wf01_v02_until_blocked(bid)
after = AgentDService().get_wf01_v02_batch(bid)

def execution(report):
    # Report timestamp/export details can change on read; scientific execution must not.
    return report.get('m6_execution')

old, new = execution(before), execution(after)
assert old is not None, 'Missing m6_execution; check report schema before claiming recovery'
assert old == new, 'Scientific execution changed when resuming terminal design-only task'
result = {'batch_id': bid, 'status': 'VERIFIED', 'scope': 'terminal design-only reload/idempotence',
          'm6_execution_sha256': hashlib.sha256(json.dumps(new, sort_keys=True).encode()).hexdigest()}
(root / 'offline-acceptance-resume-20261012.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
