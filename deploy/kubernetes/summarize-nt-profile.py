"""Export engineering metrics only; never print query or hit content."""
import json
from pathlib import Path
root=Path('/evidence/nt-profile-r06')
result=json.loads((root/'result.json').read_text())
samples=[json.loads(line) for line in (root/'samples.jsonl').read_text().splitlines()]
last=samples[-1]['cgroup']
def io(v):
    return sum(int(t.split('=')[1]) for line in (v or '').splitlines() for t in line.split() if t.startswith('rbytes='))
def pairs(v):
    return dict(line.split() for line in (v or '').splitlines())
out={k:result[k] for k in ['status','returncode','elapsed_seconds','timeout_seconds','threads']}
out.update(sample_count=len(samples),sampled_peak_memory_GiB=round(max(int(s['cgroup'].get('memory.current') or 0) for s in samples)/2**30,3),read_GiB=round(io(last.get('io.stat'))/2**30,3),cpu_seconds=round(int(pairs(last.get('cpu.stat')).get('usage_usec',0))/1e6,3),memory_events=pairs(last.get('memory.events')),scope='full database execution and performance only; workflow acceptance pending')
print(json.dumps(out))
raise SystemExit(0 if result['status']=='COMPLETED' and result['returncode']==0 else 1)
