"""Four-volume benchmark only; never a replacement for full-nt acceptance.

No process deadline. No global cache drop. Raw/canonical hits and resource
samples are retained; partitioned results require separate equivalence review.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

CASE = os.environ['BENCH_CASE']
SOURCE = Path('/data/blastdb/nt-20261006')
ROOT = Path('/evidence/nt-subset-20261012-r04')
OUT = ROOT / CASE
OUT.mkdir(parents=True, exist_ok=True)
VOLUMES = ['000', '100', '200', '398']
DBSIZE = '4766986716743'
FIELDS = '6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qseq sseq sstrand stitle'


def save(path, data):
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(path)


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def cg():
    return {key: read('/sys/fs/cgroup/'+key) for key in
            ['memory.current','memory.peak','memory.max','memory.events','memory.stat','cpu.stat','io.stat']}


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(str(command)+': '+result.stderr[-2000:])
    return result.stdout


def direct_read(database, label):
    measurements = []
    for volume in VOLUMES:
        command = ['dd', 'if='+str(database/f'nt.{volume}.nsq'), 'of=/dev/null',
                   'bs=4M', 'count=256', 'iflag=direct', 'status=none']
        start = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True)
        elapsed = time.monotonic()-start
        measurements.append({'volume':volume,'seconds':elapsed,'returncode':result.returncode,
                             'MiB_per_second':1024/elapsed if result.returncode == 0 else None,
                             'stderr':result.stderr[-1000:]})
    save(OUT/(label+'-direct-read.json'),measurements)
    print(json.dumps({'event':'DIRECT_READ','storage':label,'measurements':measurements}),flush=True)


def make_queries():
    queries = '>CSFV_F\nATGCCCAYAGTAGGACTAGCA\n>CSFV_R\nTCAACTCCATGTGCCATGTAC\n>CSFV_P\nCCTGAGTACAGGACAGTCGTCA\n'
    provenance = []
    for volume in VOLUMES:
        # Read one accession from each volume, then stop the listing child.
        proc = subprocess.Popen(['blastdbcmd','-db',str(SOURCE/f'nt.{volume}'),'-entry','all','-outfmt','%a'],
                                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        try:
            accession = proc.stdout.readline().strip()
        finally:
            proc.terminate(); proc.wait(); proc.stdout.close()
        assert accession, 'No accession in volume '+volume
        sequence = run(['blastdbcmd','-db',str(SOURCE/f'nt.{volume}'),'-entry',accession,'-range','1-100000','-outfmt','%s']).replace('\n','').strip().upper()
        offset = next((i for i in range(len(sequence)-39)
                       if set(sequence[i:i+40]) <= set('ACGT') and len(set(sequence[i:i+40]))==4
                       and max(sequence[i:i+40].count(b) for b in 'ACGT') <= 20
                       and len({sequence[j:j+3] for j in range(i,i+38)}) >= 26),None)
        assert offset is not None, 'No valid positive-control interval: '+accession
        sequence=sequence[offset:offset+40]
        queries += f'>CONTROL_{volume}\n{sequence}\n'
        provenance.append({'volume':volume,'accession':accession,'sequence':sequence,'start_1based':offset+1,'end_1based':offset+40})
    save(ROOT/'positive-controls.json',provenance)
    (ROOT/'queries.fasta').write_text(queries)


def measure(database, name, threads, partitioned=False):
    path=OUT/name
    path.mkdir(exist_ok=True)
    prefixes = [str(database/f'nt.{v}') for v in VOLUMES]
    commands=[]
    if partitioned:
        databases=prefixes
    else:
        run(['blastdb_aliastool','-dblist',' '.join(prefixes),'-dbtype','nucl','-out',str(path/'subset'),'-title','Four-volume performance fixture'])
        databases=[str(path/'subset')]
    for db in databases:
        commands.append(['blastn','-task','blastn-short','-query',str(ROOT/'queries.fasta'),
                         '-db',db,'-dbsize',DBSIZE,'-evalue','0.00001','-num_threads',str(threads),
                         '-max_target_seqs','100','-outfmt',FIELDS])
    # Evict only our isolated copied files; never drop global/production caches.
    for source in database.iterdir():
        if source.is_file():
            with source.open('rb') as handle:
                os.posix_fadvise(handle.fileno(),0,0,os.POSIX_FADV_DONTNEED)
    start=time.monotonic(); before=cg(); processes=[]; handles=[]; peaks=[]
    for i,command in enumerate(commands):
        out=(path/f'hits-{i}.tsv').open('w'); err=(path/f'stderr-{i}.txt').open('w')
        handles.extend([out,err]); processes.append(subprocess.Popen(command,stdout=out,stderr=err))
    while any(p.poll() is None for p in processes):
        sample={'seconds':round(time.monotonic()-start,2),'cgroup':cg(),
                'processes':[{'pid':p.pid,'status':read(f'/proc/{p.pid}/status'),'io':read(f'/proc/{p.pid}/io')} for p in processes]}
        with (path/'samples.jsonl').open('a') as f: f.write(json.dumps(sample)+'\n')
        if sample['cgroup']['memory.current']: peaks.append(int(sample['cgroup']['memory.current']))
        time.sleep(0.2)
    codes=[p.wait() for p in processes]
    for handle in handles:handle.close()
    elapsed=time.monotonic()-start
    lines=[]
    for p in path.glob('hits-*.tsv'): lines.extend(x for x in p.read_text().splitlines() if x)
    canonical='\n'.join(sorted(set(lines)))
    (path/'hits-canonical.tsv').write_text(canonical)
    result={'name':name,'case':CASE,'seconds':round(elapsed,3),'threads_per_process':threads,
            'processes':len(processes),'returncodes':codes,'status':'COMPLETED' if all(c==0 for c in codes) else 'FAILED',
            'hit_rows':len(lines),'unique_hit_rows':len(set(lines)),
            'positive_controls_hit':sorted(set(x.split('\t')[0] for x in lines if x.startswith('CONTROL_'))),
            'canonical_sha256':hashlib.sha256(canonical.encode()).hexdigest(),
            'sampled_cgroup_peak_bytes':max(peaks,default=0),'before':before,'after':cg(),'commands':commands,
            'statistics_note':'dbsize fixed to full nt; sharded top-N/evalue equivalence is not assumed'}
    save(path/'result.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['before','after','commands']}),flush=True)
    return result


try:
    save(OUT/'environment.json',{'case':CASE,'volumes':VOLUMES,'cgroup':cg(),
         'df':run(['df','-hT','/scratch','/data/blastdb/nt-20261006','/evidence']),
         'blast_version':run(['blastn','-version']),
         'cache_policy':'Private copied files; fsync then per-file POSIX_FADV_DONTNEED before each scan; no global cache eviction. Advisory eviction is not a guaranteed hardware cold cache.'})
    if CASE=='ceph-8gi':make_queries()
    assert (ROOT/'queries.fasta').exists()
    database=Path('/scratch/db') if CASE=='local-32gi' else OUT/'db-copy'
    database.mkdir(exist_ok=True)
    if True:
        files=[p for v in VOLUMES for p in SOURCE.glob(f'nt.{v}.*') if p.is_file() and '.tar' not in p.name]
        # Each v5 volume also depends on shared nt lookup files. Do not copy the
        # full-399-volume alias; our explicit four-volume alias is built below.
        files += [SOURCE/('nt.'+suffix) for suffix in ['ndb','nos','not','ntf','nto'] if (SOURCE/('nt.'+suffix)).is_file()]
        size=sum(p.stat().st_size for p in files)
        assert shutil.disk_usage(database).free > size+10*2**30
        started=time.monotonic()
        copied=[]
        for p in files:
            target=database/p.name
            digest=hashlib.sha256()
            with p.open('rb') as src,target.open('wb') as dst:
                while block:=src.read(8*2**20):
                    digest.update(block);dst.write(block)
                dst.flush();os.fsync(dst.fileno())
            with target.open('rb') as f:
                verified=hashlib.file_digest(f,'sha256').hexdigest()
                os.posix_fadvise(f.fileno(),0,0,os.POSIX_FADV_DONTNEED)
            assert verified==digest.hexdigest(), 'Copy checksum mismatch: '+p.name
            copied.append({'name':p.name,'bytes':p.stat().st_size,'sha256':verified})
            print(json.dumps({'event':'COPY_VERIFIED','name':p.name,'bytes':p.stat().st_size}),flush=True)
        save(OUT/'copy.json',{'bytes':size,'seconds':time.monotonic()-started,'files':copied,
                            'storage':'node emptyDir disk; NOT verified NVMe' if CASE=='local-32gi' else 'private copy on hosts Ceph RBD PVC'})
        for v in VOLUMES:
            run(['blastdbcmd','-db',str(database/f'nt.{v}'),'-info'])
    direct_read(database,CASE)
    # Reverse order in the repeat to expose order/cache effects.
    plans=[('single-4',4,False),('single-16',16,False),('split-4x4',4,True)]
    results=[]
    for repeat,plan in [(1,plans),(2,list(reversed(plans)))]:
        for name,threads,split in plan:
            results.append(measure(database,f'r{repeat}-{name}',threads,split))
    baseline=results[0]['canonical_sha256']
    for result in results:result['exact_equal_to_first']=result['canonical_sha256']==baseline
    save(OUT/'summary.json',{'status':'COMPLETED' if all(r['status']=='COMPLETED' for r in results) else 'FAILED',
                           'results':results,'scope':'Four-volume diagnostic, not full-nt acceptance'})
    print('BENCHMARK_FINISHED '+CASE,flush=True)
except Exception as exc:
    save(OUT/'summary.json',{'status':'FAILED','error':str(exc)})
    raise
