"""Acquire auditable, same-species P5 templates; run only during provisioning.

Runtime never calls this downloader. Originals and feature coordinates are retained.
Distinct sequences are evidence of sequence diversity, not proof of five breeds.
"""
import hashlib
import io
import json
import os
import time
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from Bio import SeqIO
from Bio.Seq import Seq

ROOT = Path(os.environ.get("INTERNAL_ROOT", "/data/internal-controls-20261012-r01"))
SPECIES = {"pig": (9823, "Sus scrofa"), "cattle": (9913, "Bos taurus"), "chicken": (9031, "Gallus gallus")}
REFERENCES = {
    "pig": {"ACTB": "NM_001444420.1", "GAPDH": "NM_001206359.1", "18S rRNA": "NR_046261.1"},
    "cattle": {"ACTB": "NM_173979.3", "GAPDH": "NM_001034034.2", "18S rRNA": "NR_036642.1"},
    "chicken": {"ACTB": "NM_205518.2", "GAPDH": "NM_204305.1", "18S rRNA": "AF173612.1"},
}
TAXA = {}


def same_species(source, taxid):
    actual = next((v.split(':', 1)[1] for v in source.qualifiers.get('db_xref', []) if v.startswith('taxon:')), None)
    if actual == str(taxid):
        return True
    if actual is None:
        return False
    if actual not in TAXA:
        cache = ROOT / f'taxonomy-{actual}.xml'
        if not cache.exists():
            cache.write_bytes(request('efetch.fcgi', db='taxonomy', id=actual))
        tree = ET.fromstring(cache.read_bytes())
        TAXA[actual] = {t.text for t in tree.findall('.//LineageEx/Taxon/TaxId')}
    return str(taxid) in TAXA[actual]


def request(endpoint, **params):
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/" + endpoint + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        time.sleep(0.4 + attempt * 2)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "OligoSmith-offline-provisioning/1"}), timeout=90) as response:
                return response.read()
        except Exception:
            if attempt == 4:
                raise


def extract(record, taxid, gene):
    source = next((f for f in record.features if f.type == "source"), None)
    if source is None or not same_species(source, taxid):
        return None
    candidates = []
    for feature in record.features:
        q = feature.qualifiers
        if "pseudo" in q or "pseudogene" in q:
            continue
        names = " ".join(q.get("gene", []) + q.get("gene_synonym", [])).upper().replace(";", " ").split()
        product = " ".join(q.get("product", [])).lower()
        product_matches = (gene == 'ACTB' and any(v in product for v in ('beta-actin', 'beta actin', 'actin beta', 'actin, cytoplasmic 1'))) or (gene == 'GAPDH' and any(v in product for v in ('glyceraldehyde-3-phosphate dehydrogenase', 'glyceraldehyde 3-phosphate dehydrogenase')))
        matches = (feature.type == "CDS" and (gene in names or product_matches)) if gene != "18S rRNA" else (feature.type == "rRNA" and "18s" in product)
        if matches:
            seq = str(feature.extract(record.seq)).upper()
            if set(seq) <= set("ACGT") and len(seq) >= 80:
                candidates.append((seq, str(feature.location)))
    if len(candidates) != 1:
        return None
    seq, location = candidates[0]
    if gene != "18S rRNA":
        # Retain deposited transcript diversity including UTRs, as the runtime
        # Entrez provider does. CDS identity alone does not deduplicate mRNAs.
        if any("rna" in v.lower() for v in source.qualifiers.get("mol_type", [])):
            seq = str(record.seq).upper()
            location = f"full transcript; verified CDS {location}"
        if not set(seq) <= set("ACGT"):
            return None
    return seq, {"accession": record.id, "description": record.description, "organism": record.annotations.get("organism"), "taxid": taxid, "feature_location": location, "source_qualifiers": source.qualifiers, "sha256": hashlib.sha256(seq.encode()).hexdigest()}


def transcript_homolog(record, taxid, reference):
    """Keep annotated GAPDH transcript fragments only with local alignment evidence."""
    source = next((f for f in record.features if f.type == 'source'), None)
    title = record.description.lower()
    seq = str(record.seq).upper()
    if source is None or not same_species(source, taxid) or 'pseudo' in title:
        return None
    if not ('gapdh' in title or ('glyceraldehyde' in title and 'dehydrogenase' in title)):
        return None
    if len(seq) < 80 or not set(seq) <= set('ACGT'):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        q, s = Path(tmp)/'q.fa', Path(tmp)/'s.fa'
        q.write_text('>query\n'+seq+'\n'); s.write_text('>reference\n'+reference+'\n')
        run = subprocess.run(['blastn','-query',str(q),'-subject',str(s),'-dust','no','-outfmt','6 pident length qcovhsp sstrand','-max_hsps','1'],capture_output=True,text=True,check=True,timeout=30)
    rows = [line.split() for line in run.stdout.splitlines()]
    hit = next((row for row in rows if float(row[0]) >= 98 and int(row[1]) >= 80 and int(row[2]) >= 90), None)
    if hit is None:
        return None
    if hit[3] == 'minus':
        seq = str(Seq(seq).reverse_complement())
    return seq, {'accession':record.id,'description':record.description,'organism':record.annotations.get('organism'),'taxid':taxid,'feature_location':'deposited transcript fragment; orientation normalized by BLAST','source_qualifiers':source.qualifiers,'homology_evidence':{'pident':float(hit[0]),'length':int(hit[1]),'qcovhsp':int(hit[2]),'sstrand':hit[3]},'sha256':hashlib.sha256(seq.encode()).hexdigest()}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "NCBI nucleotide GenBank", "diversity_scope": "distinct deposited sequences; breed coverage not established", "resources": {}}
    failures = []
    for species, (taxid, organism) in SPECIES.items():
        for gene, reference in REFERENCES[species].items():
            folder = ROOT / species / gene.replace(" ", "_")
            folder.mkdir(parents=True, exist_ok=True)
            clause = {'18S rRNA': '("18S ribosomal RNA"[Title] OR "18S rRNA"[Title])', 'ACTB': '(ACTB[Gene Name] OR "beta actin"[Title] OR "beta-actin"[Title])', 'GAPDH': '(GAPDH[Gene Name] OR GAPDH[Title] OR GAPD[Title] OR G3PDH[Title] OR (glyceraldehyde[Title] AND dehydrogenase[Title]))'}[gene]
            query = f'({clause}) AND txid{taxid}[Organism:exp] AND 80:10000[SLEN] NOT "whole genome shotgun"[Title] NOT intron[Title] NOT EST[filter] NOT PREDICTED[Title]'
            search = json.loads(request("esearch.fcgi", db="nuccore", term=query, retmode="json", retmax=500, sort="relevance"))
            (folder / "search.json").write_text(json.dumps(search, indent=2))
            ids = [reference, *search["esearchresult"]["idlist"]]
            selected = []
            sequences = set()
            reference_ok = False
            # Fetch the pinned reference alone: EFetch can collapse historical
            # and current versions of one UID in a batch into the current one.
            blocks = [[reference]] + [ids[offset:offset+25] for offset in range(1, len(ids), 25)]
            for block in blocks:
                block_ids = ",".join(block)
                cache = folder / f"source-{hashlib.sha256(block_ids.encode()).hexdigest()[:16]}.gb"
                if not cache.exists():
                    cache.write_bytes(request("efetch.fcgi", db="nuccore", id=block_ids, rettype="gb", retmode="text"))
                for record in SeqIO.parse(io.StringIO(cache.read_text()), "genbank"):
                    extracted = extract(record, taxid, gene)
                    if not extracted:
                        continue
                    seq, info = extracted
                    if record.id == reference:
                        reference_ok = True
                    if seq not in sequences:
                        selected.append((seq, info))
                        sequences.add(seq)
                if reference_ok and len(selected) >= 10:
                    break
            key = f"internal:{species}:{gene}:sequences"
            if gene == 'GAPDH' and reference_ok and len(selected) < 5:
                extra_query = f'({clause}) AND txid{taxid}[Organism:exp] AND 80:10000[SLEN] NOT intron[Title]'
                extra = json.loads(request('esearch.fcgi', db='nuccore', term=extra_query, retmode='json', retmax=150, sort='relevance'))
                (folder/'homolog-search.json').write_text(json.dumps(extra,indent=2))
                extra_ids = extra['esearchresult']['idlist']
                for offset in range(0,len(extra_ids),25):
                    block_ids = ','.join(extra_ids[offset:offset+25])
                    cache = folder/f'source-{hashlib.sha256(block_ids.encode()).hexdigest()[:16]}.gb'
                    if not cache.exists():
                        cache.write_bytes(request('efetch.fcgi',db='nuccore',id=block_ids,rettype='gb',retmode='text'))
                    for record in SeqIO.parse(io.StringIO(cache.read_text()),'genbank'):
                        extracted = transcript_homolog(record,taxid,selected[0][0])
                        if extracted and extracted[0] not in sequences:
                            selected.append(extracted);sequences.add(extracted[0])
                    if len(selected) >= 10:
                        break
            entry = {"reference_accession": reference, "query": query, "sequence_count": len(selected), "records": [info for _, info in selected], "status": "READY" if reference_ok and len(selected) >= 5 else "INSUFFICIENT_VALIDATED_SEQUENCES"}
            if entry["status"] == "READY":
                fasta = folder / "sequences.fasta"
                fasta.write_text("".join(f'>{info["accession"]} {organism} {gene} feature={info["feature_location"]}\n{seq}\n' for seq, info in selected))
                entry["fasta_sha256"] = hashlib.sha256(fasta.read_bytes()).hexdigest()
                entry["relative_path"] = str(fasta.relative_to(ROOT))
            else:
                failures.append(key)
            manifest["resources"][key] = entry
            (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            print(key, entry["status"], len(selected), flush=True)
    if failures:
        raise RuntimeError("Not publishable: " + ", ".join(failures))
    (ROOT / "DOWNLOAD_COMPLETE").write_text(manifest["retrieved_at"])
    print("INTERNAL_CONTROLS_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
