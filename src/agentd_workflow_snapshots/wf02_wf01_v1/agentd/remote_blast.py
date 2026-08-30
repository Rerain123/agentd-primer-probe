"""Remote NCBI BLAST MCP adapter for WF-01 background specificity.

The independent BLAST service exposes MCP over HTTP/SSE and returns NCBI
JSON2_S-derived hits.  Its current response does not retain the originating
FASTA query id, so this adapter deterministically assigns each hit by comparing
the returned aligned query sequence with the submitted oligos.  Ambiguous or
unmappable hits make the whole search invalid instead of being silently used
for a scientific decision.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from .errors import ToolUnavailableError
from .sequence import normalize_sequence
from .specificity import BlastAdapter


REMOTE_BLAST_RESOURCE_VERSION = "NCBI_NT_PUBLIC_LIVE"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int_env(name: str, default: int, *, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return min(maximum, max(1, value))


class NCBIBlastMCPProvider:
    """Call ``ncbi_blast_search`` and normalize its hits to AgentD evidence."""

    def __init__(
        self,
        service_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
        max_seqs: int | None = None,
    ) -> None:
        raw_url = (
            service_url
            if service_url is not None
            else os.environ.get("BLAST_SEARCH_SERVICE_URL", "")
        )
        self.service_url = str(raw_url or "").strip().rstrip("/")
        self.timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else _positive_float_env("BLAST_SEARCH_TIMEOUT_SECONDS", 360.0)
        )
        self.max_seqs = (
            min(500, max(1, int(max_seqs)))
            if max_seqs is not None
            else _positive_int_env("BLAST_SEARCH_MAX_SEQS", 100, maximum=500)
        )
        self.last_search_evidence: dict[str, Any] = {}

    @property
    def configured(self) -> bool:
        if not self.service_url:
            return False
        parsed = urlsplit(self.service_url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @property
    def sse_url(self) -> str:
        return f"{self.service_url}/sse"

    def status(self) -> dict[str, Any]:
        return {
            "tool": "NCBI_BLAST_MCP",
            "configured": self.configured,
            "service_url": self.service_url or None,
            "database": "nt" if self.configured else None,
            "database_version": (
                REMOTE_BLAST_RESOURCE_VERSION if self.configured else None
            ),
            "version_semantics": (
                "LIVE_PUBLIC_DATABASE_NOT_A_FIXED_SNAPSHOT"
                if self.configured
                else None
            ),
            "timeout_seconds": self.timeout_seconds,
            "max_seqs": self.max_seqs,
        }

    def resource(self, *, scope: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": "nt:public:remote-mcp",
            "kind": "remote_blast_service",
            "path": None,
            "uri": self.sse_url if self.configured else None,
            "version": REMOTE_BLAST_RESOURCE_VERSION,
            "version_valid": self.configured,
            "exists": None,
            "scope": copy.deepcopy(scope),
            "resolution_status": "RESOLVED" if self.configured else "NOT_RESOLVED",
            "resolution_reason": None if self.configured else "REMOTE_SERVICE_NOT_CONFIGURED",
            "version_semantics": "LIVE_PUBLIC_DATABASE_NOT_A_FIXED_SNAPSHOT",
        }

    async def _call_tool_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
        except ImportError as exc:  # pragma: no cover - deployment extra boundary
            raise ToolUnavailableError(
                "远程 BLAST 所需的 MCP 客户端不可用",
                details={"exception_type": type(exc).__name__},
            ) from exc

        read_timeout = timedelta(seconds=self.timeout_seconds)
        try:
            async with sse_client(
                self.sse_url,
                timeout=min(30.0, self.timeout_seconds),
                sse_read_timeout=self.timeout_seconds,
            ) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=read_timeout,
                ) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "ncbi_blast_search",
                        arguments,
                        read_timeout_seconds=read_timeout,
                    )
        except Exception as exc:
            raise ToolUnavailableError(
                "远程 NCBI BLAST MCP 调用失败",
                details={
                    "service_url": self.service_url,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                },
            ) from exc

        if bool(getattr(result, "isError", False)):
            raise ToolUnavailableError(
                "远程 NCBI BLAST MCP 返回工具错误",
                details={"service_url": self.service_url},
            )
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            payload = structured
        else:
            texts = [
                str(getattr(item, "text", ""))
                for item in getattr(result, "content", [])
                if getattr(item, "type", None) == "text"
            ]
            if not texts:
                raise ToolUnavailableError("远程 NCBI BLAST MCP 没有返回 JSON 文本")
            try:
                payload = json.loads("\n".join(texts))
            except json.JSONDecodeError as exc:
                raise ToolUnavailableError(
                    "远程 NCBI BLAST MCP 返回的内容不是有效 JSON",
                    details={"message": str(exc)},
                ) from exc
        if not isinstance(payload, dict):
            raise ToolUnavailableError("远程 NCBI BLAST MCP 返回结构无效")
        if payload.get("error") is True:
            raise ToolUnavailableError(
                "远程 NCBI BLAST 搜索失败",
                details={"message": str(payload.get("message") or "unknown error")},
            )
        if not isinstance(payload.get("hits"), list):
            raise ToolUnavailableError("远程 NCBI BLAST 响应缺少 hits 数组")
        return payload

    def _call_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._call_tool_async(arguments))

        # FastMCP currently runs this synchronous service callback in a worker
        # thread, but direct library consumers may call it from an async task.
        # Run the client loop in one bounded helper thread in that case instead
        # of nesting asyncio.run() in the active loop.
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                outcome["result"] = asyncio.run(self._call_tool_async(arguments))
            except BaseException as exc:  # propagated on the caller thread
                outcome["error"] = exc

        thread = threading.Thread(
            target=run,
            name="agentd-ncbi-blast-mcp",
            daemon=True,
        )
        thread.start()
        thread.join(self.timeout_seconds + 30.0)
        if thread.is_alive():
            raise ToolUnavailableError(
                "远程 NCBI BLAST MCP 调用超时",
                details={"service_url": self.service_url},
            )
        error = outcome.get("error")
        if isinstance(error, BaseException):
            raise error
        result = outcome.get("result")
        if not isinstance(result, dict):
            raise ToolUnavailableError("远程 NCBI BLAST MCP 未返回结果")
        return result

    @staticmethod
    def _fasta(queries: dict[str, str]) -> str:
        lines: list[str] = []
        for query_id, sequence in queries.items():
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(query_id)).strip("_")
            if not safe_id:
                raise ToolUnavailableError("远程 BLAST 查询 ID 无效")
            normalized = normalize_sequence(sequence)
            lines.extend((f">{safe_id}", normalized))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _map_query_ids(
        hit: dict[str, Any],
        queries: dict[str, str],
    ) -> list[str]:
        alignment = hit.get("alignment") or {}
        aligned_query = str(alignment.get("query_seq") or "").upper()
        ungapped = aligned_query.replace("-", "")
        if not ungapped:
            raise ToolUnavailableError("远程 BLAST 命中缺少 query_seq，无法关联候选")
        try:
            query_start = int(hit.get("query_start", 1))
            query_end = int(hit.get("query_end", query_start + len(ungapped) - 1))
        except (TypeError, ValueError) as exc:
            raise ToolUnavailableError("远程 BLAST 命中的查询坐标无效") from exc

        matched: list[str] = []
        for query_id, sequence in queries.items():
            normalized = normalize_sequence(sequence)
            lower = min(query_start, query_end) - 1
            upper = max(query_start, query_end)
            expected = normalized[lower:upper]
            if expected == ungapped or ungapped in normalized:
                matched.append(query_id)
        if not matched:
            raise ToolUnavailableError(
                "远程 BLAST 命中无法关联到任何 primer/probe 查询",
                details={
                    "matched_query_ids": matched,
                    "query_start": query_start,
                    "query_end": query_end,
                    "aligned_query": aligned_query,
                },
            )
        # Different candidates may legitimately contain the same oligo. NCBI's
        # wrapper drops the FASTA query id, so one returned hit is evidence for
        # every byte-identical submitted query.  Non-identical partial matches
        # remain ambiguous and must not be duplicated across candidates.
        full_sequences = {normalize_sequence(queries[item]) for item in matched}
        if len(matched) > 1 and len(full_sequences) != 1:
            raise ToolUnavailableError(
                "远程 BLAST 局部命中无法唯一关联到 primer/probe 查询",
                details={
                    "matched_query_ids": matched,
                    "query_start": query_start,
                    "query_end": query_end,
                    "aligned_query": aligned_query,
                },
            )
        return matched

    @staticmethod
    def _scientific_name(title: str) -> str | None:
        bracketed = re.findall(
            r"\[([A-Z][a-z]+(?:\s+[a-z][A-Za-z0-9_.-]*)+)\]",
            title,
        )
        if bracketed:
            return bracketed[-1]
        cleaned = re.sub(r"^PREDICTED:\s*", "", title, flags=re.IGNORECASE)
        match = re.match(r"([A-Z][a-z]+\s+[a-z][A-Za-z0-9_.-]+)\b", cleaned)
        return match.group(1) if match else None

    @classmethod
    def normalize_hits(
        cls,
        raw_hits: list[dict[str, Any]],
        queries: dict[str, str],
    ) -> list[dict[str, Any]]:
        normalized_hits: list[dict[str, Any]] = []
        for raw in raw_hits:
            if not isinstance(raw, dict):
                raise ToolUnavailableError("远程 NCBI BLAST hits 含无效条目")
            query_ids = cls._map_query_ids(raw, queries)
            alignment = raw.get("alignment") or {}
            aligned_query = str(alignment.get("query_seq") or "").upper()
            aligned_subject = str(alignment.get("subject_seq") or "").upper()
            if not aligned_subject or len(aligned_query) != len(aligned_subject):
                raise ToolUnavailableError("远程 BLAST 命中的比对序列不完整")
            covered_query_bases = sum(base != "-" for base in aligned_query)
            mismatches = sum(
                1
                for query_base, subject_base in zip(aligned_query, aligned_subject)
                if query_base == "-" or subject_base == "-" or query_base != subject_base
            )
            query_start = int(raw.get("query_start", 1))
            query_end = int(raw.get("query_end", query_start))
            subject_start = int(raw.get("subject_start", 1))
            subject_end = int(raw.get("subject_end", subject_start))
            hsps = raw.get("hsps") or []
            best_hsp = hsps[0] if hsps and isinstance(hsps[0], dict) else {}
            title = str(
                raw.get("subject_desc")
                or raw.get("subject_full_def")
                or ""
            )
            for query_id in query_ids:
                query = normalize_sequence(queries[query_id])
                normalized_hits.append(
                    {
                        "query_id": query_id,
                        "accession": str(
                            raw.get("subject_accession")
                            or raw.get("subject_id")
                            or ""
                        ),
                        "identity_pct": float(raw.get("identity_pct", 0.0)),
                        "alignment_length": int(
                            raw.get("align_length", len(aligned_query))
                        ),
                        "query_coverage_pct": round(
                            covered_query_bases / len(query) * 100,
                            3,
                        ),
                        "mismatches": mismatches,
                        "gap_opens": int(raw.get("gaps", 0)),
                        "query_start": query_start,
                        "query_end": query_end,
                        "subject_start": subject_start,
                        "subject_end": subject_end,
                        "evalue": float(raw.get("evalue", 1.0)),
                        "bitscore": float(
                            best_hsp.get("bit_score", raw.get("score", 0.0))
                        ),
                        "hit_sequence": aligned_subject.replace("-", ""),
                        "strand": (
                            "plus" if subject_end >= subject_start else "minus"
                        ),
                        "title": title,
                        "scientific_name": cls._scientific_name(title),
                        "three_prime_mismatches": (
                            BlastAdapter._three_prime_mismatches(
                                query,
                                aligned_query,
                                aligned_subject,
                                query_start,
                                query_end,
                            )
                        ),
                        "thermodynamics": None,
                        "raw_remote_hit": copy.deepcopy(raw),
                    }
                )
        return normalized_hits

    def search(
        self,
        queries: dict[str, str],
        *,
        evalue: float,
        exclude_organisms: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ToolUnavailableError(
                "远程 BLAST 服务未配置",
                details={"environment": "BLAST_SEARCH_SERVICE_URL"},
            )
        arguments: dict[str, Any] = {
            "query": self._fasta(queries),
            "database": "nt",
            "program": "blastn",
            "evalue": float(evalue),
            "megablast": False,
            "max_seqs": self.max_seqs,
        }
        exclusions = [str(item).strip() for item in (exclude_organisms or []) if str(item).strip()]
        if exclusions:
            arguments["exclude_organisms"] = exclusions
        started_at = _utc_now()
        try:
            raw = self._call_tool(arguments)
            hits = self.normalize_hits(raw["hits"], queries)
        except Exception as exc:
            self.last_search_evidence = {
                "tool": "NCBI_BLAST_MCP",
                "status": "FAILED",
                "service_url": self.service_url,
                "database": "nt",
                "database_version": REMOTE_BLAST_RESOURCE_VERSION,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "arguments": copy.deepcopy(arguments),
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
            raise
        self.last_search_evidence = {
            "tool": "NCBI_BLAST_MCP",
            "status": "COMPLETED",
            "service_url": self.service_url,
            "database": "nt",
            "database_version": REMOTE_BLAST_RESOURCE_VERSION,
            "version_semantics": "LIVE_PUBLIC_DATABASE_NOT_A_FIXED_SNAPSHOT",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "arguments": copy.deepcopy(arguments),
            "db_total_seqs": raw.get("db_total_seqs"),
            "db_total_letters": raw.get("db_total_letters"),
            "organism_summary": copy.deepcopy(raw.get("organism_summary")),
            "total_hits": raw.get("total_hits"),
            "raw_output": copy.deepcopy(raw),
        }
        return {
            "hits": hits,
            "method": "blastn",
            "provider": "ncbi_blast_mcp",
            "tool": self.status(),
            "raw_tool_output": copy.deepcopy(self.last_search_evidence),
        }
