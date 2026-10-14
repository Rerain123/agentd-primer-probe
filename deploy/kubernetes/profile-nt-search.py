"""Full-nt diagnostic without a time limit; completion is determined by BLAST."""
import hashlib
import json
import shutil
import sys
import os
import resource
import subprocess
import time
from pathlib import Path

ROOT = Path('/evidence') / os.environ.get('PROFILE_NAME', 'nt-profile-r03')
THREADS = int(os.environ.get('PROFILE_THREADS', '4'))
ROOT.mkdir(exist_ok=False)
query = ROOT / 'query.fasta'
source_query = Path(os.environ.get('PROFILE_QUERY', '/evidence/nt-profile-r04/query.fasta'))
if not source_query.is_file() or not source_query.stat().st_size:
    raise RuntimeError('Saved benchmark input unavailable')
shutil.copyfile(source_query, query)
query_sha256 = hashlib.sha256(query.read_bytes()).hexdigest()
command = ['blastn', '-task', 'blastn-short', '-query', str(query), '-db',
           '/data/blastdb/nt-20261006/nt', '-evalue', '0.00001', '-num_threads', str(THREADS),
           '-max_target_seqs', '100', '-outfmt',
           '6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qseq sseq sstrand stitle']


def read(path):
    try:
        return Path(path).read_text()
    except (OSError, ProcessLookupError):
        return None


def snapshot(pid, start):
    status = read(f'/proc/{pid}/status') or ''
    return {
        'elapsed_seconds': round(time.monotonic() - start, 2),
        'process_memory': {line.split(':')[0]: line.split(':', 1)[1].strip()
                           for line in status.splitlines() if line.startswith(('VmRSS:', 'VmHWM:', 'VmSize:', 'Threads:'))},
        'process_io': read(f'/proc/{pid}/io'),
        'process_stat': read(f'/proc/{pid}/stat'),
        'cgroup': {name: read('/sys/fs/cgroup/' + name) for name in
                   ['memory.current', 'memory.peak', 'memory.max', 'memory.events', 'memory.stat', 'cpu.stat', 'io.stat',
                    'memory/memory.usage_in_bytes', 'memory/memory.max_usage_in_bytes', 'memory/memory.limit_in_bytes', 'memory/memory.failcnt']},
        'output_bytes': (ROOT / 'hits.tsv').stat().st_size,
    }


start = time.monotonic()
print(json.dumps({'event': 'START', 'command': command, 'timeout_seconds': None}), flush=True)
with (ROOT / 'hits.tsv').open('w') as out, (ROOT / 'stderr.txt').open('w') as err:
    proc = subprocess.Popen(command, stdout=out, stderr=err)
    while proc.poll() is None:
        sample = snapshot(proc.pid, start)
        with (ROOT / 'samples.jsonl').open('a') as log:
            log.write(json.dumps(sample) + '\n')
        print(json.dumps({k: sample[k] for k in ['elapsed_seconds', 'process_memory', 'output_bytes']}), flush=True)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    code = proc.wait()
result = {'status': 'COMPLETED' if code == 0 else 'FAILED',
          'timeout_seconds': None,
          'query_sha256': query_sha256,
          'threads': THREADS,
          'returncode': code, 'elapsed_seconds': round(time.monotonic()-start, 2),
          'ru_maxrss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
          'output_bytes': (ROOT / 'hits.tsv').stat().st_size,
          'scope': 'performance diagnostic only; not workflow scientific acceptance'}
(ROOT / 'result.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)

sys.exit(0 if code == 0 else 1)
