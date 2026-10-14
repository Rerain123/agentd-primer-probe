"""Prepare shared-coverage templates without selecting on sequence identity.

Source records may be fragments. Pick the longest gap-free reference interval
covered by at least five records, then retain *all* records covering it.
Conservation is measured afterwards. Neither bases nor acceptance thresholds
are changed. Retain raw files and per-accession source coordinates.
"""
import hashlib
import io
import json
import subprocess
from collections import Counter
from pathlib import Path
from Bio import SeqIO

source=Path('/data/internal-controls-20261012-r01')
dest=Path('/data/internal-controls-20261012-curated-r01')
dest.mkdir(exist_ok=True)
manifest=json.loads((source/'manifest.json').read_text())
assert (source/'DOWNLOAD_COMPLETE').is_file()
manifest['curation_method']='longest shared gap-free reference interval; coverage selection only, no identity filter'
for key, entry in manifest['resources'].items():
    raw=source/entry['relative_path']
    records=[]; seen=set()
    for record in SeqIO.parse(raw,'fasta'):
        accession=record.id.split('.')[0]
        if accession not in seen:
            records.append(record); seen.add(accession)
    full=dest/entry['relative_path'];full.parent.mkdir(parents=True,exist_ok=True)
    SeqIO.write(records,full,'fasta')
    run=subprocess.run(['mafft','--auto',str(full)],capture_output=True,text=True,check=True,timeout=120)
    aligned=list(SeqIO.parse(io.StringIO(run.stdout),'fasta'))
    sequences=[str(r.seq).upper() for r in aligned]
    width=len(sequences[0]); runs=[[0]*(width+1) for _ in sequences]
    for row,seq in enumerate(sequences):
        for col in range(width-1,-1,-1):
            runs[row][col]=1+runs[row][col+1] if seq[col] in 'ACGT' else 0
    best=(0,0)
    for start in range(width):
        length=min(runs[0][start],sorted((r[start] for r in runs),reverse=True)[4])
        if length>best[0]:best=(length,start)
    length,start=best
    # A version revision of the same accession is not an independent record.
    chosen=list(range(len(records)))
    interval=None
    if length>=150:
        chosen=[i for i,r in enumerate(runs) if r[start]>=length]
        unique_accessions={records[i].id.split('.')[0] for i in chosen}
        if len(unique_accessions)<5:
            length=0
    if length>=150:
        interval={'reference_accession':records[0].id,'start_1based':len(sequences[0][:start].replace('-',''))+1,'end_1based':len(sequences[0][:start+length].replace('-',''))}
        selected=[sequences[i][start:start+length] for i in chosen]
        coordinates=[{'accession':records[i].id,'start_1based':len(sequences[i][:start].replace('-',''))+1,'end_1based':len(sequences[i][:start+length].replace('-',''))} for i in chosen]
        entry['template_scope']='SHARED_REFERENCE_INTERVAL'
    else:
        chosen=list(range(len(records)))
        selected=[str(r.seq).upper() for r in records]
        coordinates=[]
        entry['template_scope']='DEPOSITED_SEQUENCES_WITH_INCOMPLETE_SHARED_COVERAGE'
    path=dest/entry['relative_path'];path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(f'>{records[i].id} scope={entry["template_scope"]}\n{seq}\n' for i,seq in zip(chosen,selected)))
    qa=subprocess.run(['mafft','--auto',str(path)],capture_output=True,text=True,check=True,timeout=120)
    qa_seqs=[str(r.seq).upper() for r in SeqIO.parse(io.StringIO(qa.stdout),'fasta')]
    score=sum(max(Counter(s[c] for s in qa_seqs if s[c] in 'ACGT').values(),default=0)/len(qa_seqs) for c in range(len(qa_seqs[0])))/len(qa_seqs[0])
    entry.update(reference_interval=interval,source_coordinates=coordinates,selected_accessions=[records[i].id for i in chosen],sequence_count=len(selected),unique_sequence_count=len(set(selected)),conservation=round(score,6),conservation_status='PASS' if score>=.99 else 'CAUTION' if score>=.97 else 'FAIL',fasta_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_fasta=str(raw))
    print(key,entry['template_scope'],len(selected),len(selected[0]),entry['conservation_status'],round(score,4),flush=True)
    (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
(dest/'CURATION_COMPLETE').write_text(manifest['retrieved_at'])
