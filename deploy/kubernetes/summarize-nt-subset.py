"""Summarize subset evidence without interpreting subset hits as full-nt PASS."""
import hashlib
import json
import os
from pathlib import Path
import statistics

root=Path('/data/internal-controls-20261012-r01')/os.environ.get('BENCH_ROOT','nt-subset-20261012-r04')
summary={'scope':'four-volume benchmark; full nt and workflow acceptance still required', 'cases':{}}
canonical={}
for name in ['ceph-8gi','ceph-32gi','local-32gi']:
    directory=root/name
    if not (directory/'summary.json').exists():
        summary['cases'][name]={'status':'RUNNING_OR_PENDING','completed_runs':[p.parent.name for p in directory.glob('*/result.json')]}
        continue
    doc=json.loads((directory/'summary.json').read_text())
    if doc['status']!='COMPLETED':
        summary['cases'][name]=doc
        continue
    grouped={}
    for result in doc['results']:
        mode=result['name'][3:]
        item=grouped.setdefault(mode,{'seconds':[],'positive_controls_hit':[],'hashes':[],'memory_peak_GiB':[],'io_GiB':[],'cpu_seconds':[]})
        item['seconds'].append(result['seconds'])
        item['positive_controls_hit'].append(result['positive_controls_hit'])
        item['hashes'].append(result['canonical_sha256'])
        item['memory_peak_GiB'].append(round(result['sampled_cgroup_peak_bytes']/2**30,3))
        def read_bytes(state):
            return sum(int(token.split('=')[1]) for line in (state.get('io.stat') or '').splitlines() for token in line.split() if token.startswith('rbytes='))
        def cpu(state):
            return int(dict(line.split() for line in state['cpu.stat'].splitlines())['usage_usec'])
        item['io_GiB'].append(round((read_bytes(result['after'])-read_bytes(result['before']))/2**30,3))
        item['cpu_seconds'].append(round((cpu(result['after'])-cpu(result['before']))/1e6,2))
        canonical[(name,result['name'])]=set((directory/result['name']/'hits-canonical.tsv').read_text().splitlines())
    for item in grouped.values():item['median_seconds']=statistics.median(item['seconds'])
    summary['cases'][name]={'status':doc['status'],'modes':grouped,'direct_read':json.loads((directory/(name+'-direct-read.json')).read_text())}
baseline=canonical.get(('ceph-8gi','r1-single-4'))
if baseline is not None:
    comparison={}
    def coords(lines):return set(tuple(row.split('\t')[:10]+row.split('\t')[11:]) for row in lines)
    for (case,run),lines in canonical.items():
        comparison[case+'/'+run]={'exact_equal':lines==baseline,'baseline_rows':len(baseline),'rows':len(lines),
                                  'missing_rows':len(baseline-lines),'extra_rows':len(lines-baseline),
                                  'equal_ignoring_evalue':coords(lines)==coords(baseline)}
    summary['comparisons']=comparison
(root/'comparison.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
