"""NCBI Entrez sequence acquisition owned by the P5 orchestrator."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from importlib.util import find_spec
from typing import Any

from .models import SequenceRecord
from .sequence import normalize_sequence


class EntrezSequenceError(RuntimeError):
    """Raised when P5 cannot obtain its built-in housekeeping sequences."""


class NCBIEntrezProvider:
    """Fetch one RefSeq template plus same-species homologs for P5.

    Biopython is imported lazily so the framework remains importable in a
    development environment without the optional scientific dependencies.
    """

    SPECIES_NAMES = {
        "pig": "Sus scrofa",
        "cattle": "Bos taurus",
        "chicken": "Gallus gallus",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        settings = config.get("internal_control", {}).get("entrez", {})
        self.enabled = bool(settings.get("enabled", True))
        self.email = str(
            os.environ.get("NCBI_ENTREZ_EMAIL")
            or settings.get("email")
            or ""
        ).strip()
        self.api_key = str(
            os.environ.get("NCBI_ENTREZ_API_KEY")
            or settings.get("api_key")
            or ""
        ).strip()
        self.tool = str(settings.get("tool") or "agentd-primer-probe").strip()
        self.retmax = max(10, int(settings.get("homolog_search_retmax", 50)))
        self.minimum_length = max(1, int(settings.get("minimum_sequence_length", 80)))
        self.max_tries = max(1, int(settings.get("max_tries", 3)))
        self.sleep_between_tries = max(
            0.0, float(settings.get("sleep_between_tries_seconds", 1.0))
        )

    @staticmethod
    def _biopython_available() -> bool:
        try:
            return find_spec("Bio.Entrez") is not None and find_spec("Bio.SeqIO") is not None
        except (ImportError, ModuleNotFoundError):
            return False

    def status(self) -> dict[str, Any]:
        dependency_available = self._biopython_available()
        return {
            "tool": "NCBI_Entrez",
            "available": self.enabled and dependency_available,
            "enabled": self.enabled,
            "biopython_available": dependency_available,
            "contact_email_configured": bool(self.email),
            "network_checked": False,
            "mode": "live_ncbi_nucleotide",
        }

    def _modules(self):
        try:
            from Bio import Entrez, SeqIO
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise EntrezSequenceError("Biopython不可用，P5无法访问NCBI Entrez") from exc
        Entrez.email = self.email or None
        Entrez.api_key = self.api_key or None
        Entrez.tool = self.tool
        Entrez.max_tries = self.max_tries
        Entrez.sleep_between_tries = self.sleep_between_tries
        return Entrez, SeqIO

    def _search_ids(self, term: str) -> list[str]:
        Entrez, _ = self._modules()
        try:
            with Entrez.esearch(
                db="nucleotide",
                term=term,
                retmax=self.retmax,
                sort="relevance",
            ) as handle:
                result = Entrez.read(handle)
        except Exception as exc:  # Biopython wraps HTTP and parse errors differently
            raise EntrezSequenceError(f"NCBI Entrez同源序列检索失败: {exc}") from exc
        return [str(item) for item in result.get("IdList", []) if str(item).strip()]

    def _fetch_fasta_records(self, identifiers: list[str]) -> list[SequenceRecord]:
        if not identifiers:
            return []
        Entrez, SeqIO = self._modules()
        try:
            with Entrez.efetch(
                db="nucleotide",
                id=",".join(identifiers),
                rettype="fasta",
                retmode="text",
            ) as handle:
                fetched = list(SeqIO.parse(handle, "fasta"))
        except Exception as exc:
            raise EntrezSequenceError(f"NCBI Entrez FASTA获取失败: {exc}") from exc

        records: list[SequenceRecord] = []
        for record in fetched:
            try:
                sequence = normalize_sequence(str(record.seq), allow_iupac=True)
            except Exception:
                continue
            if len(sequence) < self.minimum_length:
                continue
            records.append(
                SequenceRecord(
                    id=str(record.id),
                    sequence=sequence,
                    description=str(record.description or record.id),
                    source="NCBI Entrez nucleotide",
                )
            )
        return records

    def _query(self, gene: str, organism: str) -> str:
        if gene.strip().upper() in {"18S", "18S RRNA", "RNA18S"}:
            gene_clause = '(("18S ribosomal RNA"[Title]) OR ("18S rRNA"[Title]))'
        else:
            escaped = gene.replace('"', "")
            gene_clause = f'(({escaped}[Gene Name]) OR ("{escaped}"[Title]))'
        return (
            f'{gene_clause} AND ("{organism}"[Organism]) '
            f'AND ({self.minimum_length}:10000[Sequence Length])'
        )

    @staticmethod
    def _deduplicate(records: list[SequenceRecord]) -> list[SequenceRecord]:
        unique: list[SequenceRecord] = []
        seen_sequences: set[str] = set()
        for record in records:
            if record.sequence in seen_sequences:
                continue
            seen_sequences.add(record.sequence)
            unique.append(record)
        return unique

    def fetch_housekeeping_records(
        self,
        *,
        host_species: str,
        gene: str,
        refseq_accession: str,
        minimum_records: int,
    ) -> tuple[list[SequenceRecord], dict[str, Any]]:
        if not self.enabled:
            raise EntrezSequenceError("P5的NCBI Entrez序列获取未启用")
        organism = self.SPECIES_NAMES.get(host_species)
        if organism is None:
            raise EntrezSequenceError(f"P5尚未配置{host_species}的NCBI物种名称")
        if not refseq_accession:
            raise EntrezSequenceError(f"P5未配置{host_species}/{gene}的RefSeq编号")

        reference_records = self._fetch_fasta_records([refseq_accession])
        if not reference_records:
            raise EntrezSequenceError(
                f"NCBI Entrez未返回RefSeq序列: {refseq_accession}"
            )
        query = self._query(gene, organism)
        homolog_ids = self._search_ids(query)
        homolog_records = self._fetch_fasta_records(homolog_ids)
        records = self._deduplicate([reference_records[0], *homolog_records])
        if len(records) < minimum_records:
            raise EntrezSequenceError(
                f"NCBI Entrez仅获得{len(records)}条非重复{organism} {gene}序列，"
                f"至少需要{minimum_records}条"
            )
        retrieved_at = datetime.now(timezone.utc).isoformat()
        return records, {
            "method": "ncbi_entrez_live",
            "database": "NCBI nucleotide",
            "organism": organism,
            "gene": gene,
            "refseq_accession": refseq_accession,
            "query": query,
            "retrieved_at": retrieved_at,
            "sequence_count": len(records),
            "accessions": [record.id for record in records],
            "resource_key": None,
            "resource_version": f"NCBI_LIVE_{retrieved_at}",
        }
