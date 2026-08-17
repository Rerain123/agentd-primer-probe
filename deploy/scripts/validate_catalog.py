#!/usr/bin/env python3
"""Validate server database mounts without importing the Agent D package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any


BLAST_CORE_SUFFIXES = (".nhr", ".nin", ".nsq")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def host_root(env_file: Path, configured: str) -> Path:
    path = Path(configured)
    return path.resolve() if path.is_absolute() else (env_file.parent / path).resolve()


def container_to_host(container_path: str, blast_root: Path, sequence_root: Path) -> Path | None:
    path = PurePosixPath(container_path)
    mappings = ((PurePosixPath("/data/blastdb"), blast_root), (PurePosixPath("/data/sequences"), sequence_root))
    for container_root, mounted_root in mappings:
        try:
            relative = path.relative_to(container_root)
        except ValueError:
            continue
        return mounted_root.joinpath(*relative.parts)
    return None


def blast_prefix_available(prefix: Path) -> bool:
    if prefix.is_file() and prefix.suffix.lower() not in {".fa", ".fas", ".fasta", ".fna"}:
        return True
    if Path(f"{prefix}.nal").is_file():
        return True
    files = [item for item in prefix.parent.glob(f"{prefix.name}.*") if item.is_file()]
    return all(any(item.name.lower().endswith(suffix) for item in files) for suffix in BLAST_CORE_SUFFIXES)


def fasta_summary(path: Path) -> tuple[int, int]:
    records = 0
    bases = 0
    in_record = False
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            records += 1
            in_record = True
        elif in_record:
            bases += len(line)
        else:
            raise ValueError("首个非空行不是 FASTA 标题")
    if records == 0 or bases == 0:
        raise ValueError("FASTA 中没有有效序列")
    return records, bases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="验证 Agent D 服务器数据库目录",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--include-optional", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = args.env_file.resolve()
    catalog_path = args.catalog.resolve()
    try:
        env = read_env(env_file)
        catalog: dict[str, Any] = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] 无法读取部署配置: {exc}", file=sys.stderr)
        return 1

    resources = catalog.get("resources")
    if not isinstance(resources, dict):
        print("[FAIL] database-catalog.json 的 resources 必须是对象", file=sys.stderr)
        return 1

    blast_root = host_root(env_file, env.get("AGENTD_BLASTDB_DIR", "./server-data/blastdb"))
    sequence_root = host_root(env_file, env.get("AGENTD_SEQUENCE_DATA_DIR", "./server-data/sequences"))
    failures: list[str] = []
    checked = 0
    skipped = 0
    remote_background_url = env.get("BLAST_SEARCH_SERVICE_URL", "").strip()

    for key, raw_resource in sorted(resources.items()):
        if not isinstance(raw_resource, dict):
            failures.append(f"{key}: 资源定义不是对象")
            continue
        required = bool(raw_resource.get("required", False))
        if not required and not args.include_optional:
            skipped += 1
            continue
        if (
            required
            and key in {"cross:default:blast", "nt:default:blast"}
            and remote_background_url
        ):
            checked += 1
            print(
                f"[PASS] {key}: 由远程 NCBI BLAST MCP 提供 "
                f"service={remote_background_url}"
            )
            continue
        checked += 1
        version = str(raw_resource.get("version") or "").strip()
        if not version or version == "SET_ON_SERVER":
            failures.append(f"{key}: 未填写真实 version")
            continue
        container_path = raw_resource.get("path")
        if not container_path:
            failures.append(f"{key}: 当前文件型 Provider 要求配置 path")
            continue
        path = container_to_host(str(container_path), blast_root, sequence_root)
        if path is None:
            failures.append(f"{key}: 容器路径不在 /data/blastdb 或 /data/sequences 下: {container_path}")
            continue
        kind = raw_resource.get("kind")
        if kind == "blast_database":
            if not blast_prefix_available(path):
                failures.append(f"{key}: 缺少完整 BLAST 索引前缀 {path}")
                continue
            print(f"[PASS] {key}: BLAST prefix={path} version={version}")
        elif kind == "fasta":
            if not path.is_file():
                failures.append(f"{key}: FASTA 不存在 {path}")
                continue
            try:
                records, bases = fasta_summary(path)
            except (OSError, ValueError) as exc:
                failures.append(f"{key}: FASTA 无效: {exc}")
                continue
            if key.startswith("internal:") and records < 5:
                failures.append(f"{key}: 内标同源序列仅 {records} 条，至少需要 5 条")
                continue
            print(f"[PASS] {key}: FASTA records={records} bases={bases} version={version}")
        elif not path.exists():
            failures.append(f"{key}: 路径不存在 {path}")
        else:
            print(f"[PASS] {key}: path={path} version={version}")

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}", file=sys.stderr)
        print(f"[FAIL] 目录检查失败: checked={checked} failures={len(failures)} skipped_optional={skipped}", file=sys.stderr)
        return 1
    print(f"[PASS] 目录检查通过: checked={checked} skipped_optional={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
