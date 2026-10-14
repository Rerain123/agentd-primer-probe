"""Export compact scientific states without signed URLs, tokens or huge reports."""
import json
from pathlib import Path

root = Path('/data/acceptance-20261012-r01')
summary = {}
for name in ['wf01-single-design', 'wf01-multiple-template-design', 'wf01-single-full-nt',
             'wf02-two-target-internal', 'wf02-reload']:
    doc = json.loads((root / (name + '.json')).read_text())
    result = doc['result']
    item = {k: result.get(k) for k in ['status', 'batch_id']}
    item['elapsed_seconds'] = doc['elapsed_seconds']
    if name == 'wf02-two-target-internal':
        state = result['state']
        item.update(stage=state.get('stage'), terminal_report_ready=state.get('terminal_report_ready'),
                    targets=[{k: t.get(k) for k in ['target_id', 'status', 'failure', 'candidate_count']}
                             for t in state['target_design']['targets']],
                    multiplex=state.get('multiplex'), internal_control=state.get('internal_control'))
    if name == 'wf02-reload':
        item.update(snapshot_version=result.get('snapshot_version'), persisted_status=result.get('persisted_status'))
    summary[name] = item
(root / 'compact-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
