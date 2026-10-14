"""Real BLAST cache acceptance; only engineering summaries go to stdout."""
import hashlib
import json
import time
from pathlib import Path

from agentd.config import load_config
from agentd.specificity import P2BatchSearchProvider
from agentd.sequence import read_fasta

root = Path('/data/cache-acceptance-r05')
root.mkdir(exist_ok=False)
source = Path('/data/internal-controls-20261012-r01/nt-subset-20261012-r04')
queries = {record.id: record.sequence for record in read_fasta(source/'queries.fasta')}
resource = {'kind':'blast_database', 'version':'subset-fixture-v1',
            'path':str(source/'ceph-32gi/db-copy/nt.000')}
config = load_config(overrides={'tools':{'blast_runtime':{'num_threads':16, 'timeout_seconds':None},
                   'blast_cache':{'enabled':True, 'directory':str(root/'cache')}}})
results = []
baseline = None
for name in ['first', 'repeat', 'new-provider', 'version-change']:
    if name != 'repeat':
        provider = P2BatchSearchProvider(config)
    if name == 'version-change':
        resource['version'] = 'subset-fixture-v2'
    start = time.monotonic()
    result = provider.search(queries, resource, mode='RELATED_BACKGROUND_NT', evalue=1e-5)
    (root/(name+'.json')).write_text(json.dumps(result))
    canonical = hashlib.sha256(json.dumps(result['hits'],sort_keys=True).encode()).hexdigest()
    if baseline is None:
        baseline = canonical
        assert result['hits'], 'Fixture must exercise nonempty result reuse'
    assert canonical == baseline, 'Cache changed parsed results'
    cache_status = result['raw_tool_output'].get('cache',{}).get('status')
    assert cache_status == ('HIT' if name in ['repeat','new-provider'] else 'STORED')
    summary = {'case':name, 'seconds':round(time.monotonic()-start,4),
               'cache_status':cache_status, 'exact_result_equal':canonical==baseline}
    results.append(summary)
    print(json.dumps(summary),flush=True)
(root/'summary.json').write_text(json.dumps({'status':'PASSED','results':results},indent=2))
print('CACHE_ACCEPTANCE_PASSED',flush=True)
