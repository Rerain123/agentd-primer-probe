"""Measure the production BLAST short-query contract on the complete nt DB."""
import json
import resource
import subprocess
import time
from pathlib import Path

query = Path('/tmp/queries.fasta')
query.write_text('>CSFV_F\nATGCCCAYAGTAGGACTAGCA\n>CSFV_R\nTCAACTCCATGTGCCATGTAC\n>CSFV_P\nCCTGAGTACAGGACAGTCGTCA\n')
command = ['blastn', '-task', 'blastn-short', '-query', str(query), '-db', '/data/blastdb/nt-20261006/nt', '-evalue', '1000', '-max_target_seqs', '100', '-outfmt', '6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qseq sseq sstrand stitle']
start = time.monotonic()
print(json.dumps({'event':'START','command':command}), flush=True)
result = {'timeout_seconds':600, 'query_count':3, 'scientific_scope':'performance only; not an accepted primer design'}
with Path('/tmp/blast.tsv').open('w') as output:
    try:
        run = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, text=True, timeout=600)
        result.update(exit_code=run.returncode, stderr=run.stderr[-2000:], status='COMPLETED' if run.returncode == 0 else 'FAILED')
    except subprocess.TimeoutExpired:
        result.update(status='TIMED_OUT')
result.update(elapsed_seconds=round(time.monotonic()-start,2), max_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, output_bytes=Path('/tmp/blast.tsv').stat().st_size)
Path('/evidence/nt-performance.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
