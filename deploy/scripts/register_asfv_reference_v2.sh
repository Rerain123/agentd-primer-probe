#!/usr/bin/env bash
# =============================================================================
# Register the ASFV NC_044959.2 reference (Georgia 2007/1, official RefSeq)
# v2 — 修正 register_asfv_reference.sh 的两个 bug:
#   ① accession 错误: NC_001659.2(BA71V 实验室适应株) -> NC_044959.2(Georgia 2007/1 官方参考)
#   ② EXPECTED_VERSION 硬编码 1.1.2 -> 改为自动探测, 不再因版本差异 STOP
# 保留原脚本安全机制: inode 稳定写入 / rollback / validate / restart(非 recreate)
# 用法: bash register_asfv_reference_v2.sh
# 注意: 在服务器宿主机执行(不是容器内); 需要 docker 可访问
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

# --- 修正点②: 版本号自动探测, 不再硬编码 ---
# 仍要求能 exec agentd, 但版本以实际运行为准
RUNNING_VERSION="$(agentd_compose exec -T agentd python -c 'import agentd; print(agentd.__version__)' 2>/dev/null | tr -d '\r' || true)"
if [[ -z "${RUNNING_VERSION}" ]]; then
  printf '[STOP] 无法连接 agentd 容器或读取版本; 请在宿主机执行本脚本\n' >&2
  exit 1
fi
printf '[INFO] agentd 运行版本: %s\n' "${RUNNING_VERSION}"

CATALOG="${AGENTD_DEPLOY_DIR}/server-config/database-catalog.json"
BLASTDB_ROOT="$(agentd_resolve_host_path "$(agentd_read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)")"

# --- 修正点①: ASFV 官方参考基因组 Georgia 2007/1 ---
ACCESSION="NC_044959.2"
REFERENCE_DIR="${ASFV_REFERENCE_DIR:-${BLASTDB_ROOT}/target/asfv/${ACCESSION}}"
REFERENCE_FILE="${REFERENCE_DIR}/source.fasta"
QUERY_PREFIX="${REFERENCE_DIR}/db"
BACKUP=""
CATALOG_CHANGED=false
COMPLETED=false

rollback_catalog() {
  local exit_status=$?
  trap - ERR
  if [[ "${CATALOG_CHANGED}" == true && "${COMPLETED}" != true && -n "${BACKUP}" && -f "${BACKUP}" ]]; then
    printf '[WARN] Registration failed; restoring catalog from %s\n' "${BACKUP}" >&2
    # Do not replace the destination: /config/database-catalog.json is a
    # file bind mount, so its inode must remain stable for the live container.
    cp -- "${BACKUP}" "${CATALOG}" || true
    agentd_compose restart agentd || true
  fi
  exit "${exit_status}"
}
trap rollback_catalog ERR

[[ -f "${AGENTD_ENV_FILE}" ]] || { printf '[FAIL] missing %s\n' "${AGENTD_ENV_FILE}" >&2; exit 1; }
[[ -f "${CATALOG}" ]] || { printf '[FAIL] missing %s\n' "${CATALOG}" >&2; exit 1; }

install -d -m 755 "${REFERENCE_DIR}"
if [[ ! -f "${REFERENCE_FILE}" ]]; then
  printf '[INFO] 下载 %s (ASFV Georgia 2007/1 官方参考基因组)...\n' "${ACCESSION}"
  curl --fail --location --retry 3 --silent --show-error \
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=${ACCESSION}&rettype=fasta&retmode=text" \
    -o "${REFERENCE_FILE}.tmp"
  grep -q "^>${ACCESSION}" "${REFERENCE_FILE}.tmp"
  mv "${REFERENCE_FILE}.tmp" "${REFERENCE_FILE}"
fi

BASE_COUNT="$(awk '!/^>/{gsub(/[[:space:]]/, ""); total += length($0)} END {print total + 0}' "${REFERENCE_FILE}")"
# Georgia 2007/1 = 190,584 bp; 阈值 180000 留余量但仍能拦截错误/不完整下载
if (( BASE_COUNT < 180000 )); then
  printf '[STOP] 下载的 ASFV 参考文件不完整（bases=%s, 期望≈190584）\n' "${BASE_COUNT}" >&2
  exit 1
fi
printf '[INFO] 序列碱基数: %s (NC_044959.2 应为 190,584)\n' "${BASE_COUNT}"

if ! compgen -G "${QUERY_PREFIX}*.nhr" >/dev/null || \
   ! compgen -G "${QUERY_PREFIX}*.nin" >/dev/null || \
   ! compgen -G "${QUERY_PREFIX}*.nsq" >/dev/null; then
  if compgen -G "${QUERY_PREFIX}.n*" >/dev/null; then
    printf '[STOP] 发现不完整的旧查询文件，请保留该目录并发送以下输出：\n' >&2
    find "${REFERENCE_DIR}" -maxdepth 1 -type f -printf '%f\n' >&2
    exit 1
  fi
  "${SCRIPT_DIR}/build_blast_db.sh" \
    --input "${REFERENCE_FILE}" \
    --output "${QUERY_PREFIX}" \
    --title "ASFV ${ACCESSION} Georgia 2007/1"
  printf '[INFO] BLAST 数据库已构建: %s\n' "${QUERY_PREFIX}"
fi

python3 "${SCRIPT_DIR}/validate_catalog.py" \
  --env-file "${AGENTD_ENV_FILE}" \
  --catalog "${CATALOG}" >/dev/null

BACKUP="${CATALOG}.before-asfv-$(date +%Y%m%d-%H%M%S).bak"
cp -a -- "${CATALOG}" "${BACKUP}"

# Write through the existing path rather than Path.replace(): Docker file bind
# mounts retain the original inode until a container is recreated.
python3 - "${CATALOG}" <<'PY'
import json
import os
import sys
from pathlib import Path

ACCESSION = "NC_044959.2"
path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
resources = data.setdefault("resources", {})
if not isinstance(resources, dict):
    raise SystemExit("database-catalog.json 的 resources 必须是对象")

# 若旧脚本已注册过 NC_001659.2(BA71V) alias, 一并清理, 避免双库并存
for key in list(resources.keys()):
    if key.startswith("target:") and ("asfv" in key.lower() or "猪瘟" in key or "african" in key.lower() or "NC_001659" in key):
        if "NC_001659" not in key and (key.endswith(":sequences") or key.endswith(":blast")):
            # 仅清理指向旧 NC_001659 的条目
            resource = resources[key]
            if isinstance(resource, dict) and "NC_001659" in str(resource.get("version", "")):
                del resources[key]
                print(f"[INFO] 移除旧条目: {key}", flush=True)
        elif "NC_001659" in key:
            del resources[key]
            print(f"[INFO] 移除旧条目: {key}", flush=True)

names = (
    "ASFV",
    "asfv",
    "African swine fever virus",
    "非洲猪瘟病毒",
    "非洲猪瘟",
    ACCESSION,
)
blast_resource = {
    "kind": "blast_database",
    "path": f"/data/blastdb/target/asfv/{ACCESSION}/db",
    "version": ACCESSION,
    "required": False,
}
sequence_resource = {
    "kind": "fasta",
    "path": f"/data/blastdb/target/asfv/{ACCESSION}/source.fasta",
    "version": ACCESSION,
    "required": False,
}
for name in names:
    resources[f"target:{name}:blast"] = dict(blast_resource)
    resources[f"target:{name}:sequences"] = dict(sequence_resource)

rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
temporary = path.with_name(path.name + ".asfv.tmp")
with temporary.open("w", encoding="utf-8") as handle:
    handle.write(rendered)
    handle.flush()
    os.fsync(handle.fileno())

# Preserve the catalog file inode.
with path.open("w", encoding="utf-8") as handle:
    handle.write(rendered)
    handle.flush()
    os.fsync(handle.fileno())
temporary.unlink()
PY
CATALOG_CHANGED=true

python3 "${SCRIPT_DIR}/validate_catalog.py" \
  --env-file "${AGENTD_ENV_FILE}" \
  --catalog "${CATALOG}" \
  --resource-key 'target:ASFV:blast' \
  --resource-key 'target:ASFV:sequences'

# restart(非 recreate): 不替换容器、不换网络端点、不换挂载 inode
agentd_compose restart agentd

deadline=$((SECONDS + 90))
while ! agentd_compose exec -T agentd python - <<'PY'
from agentd.api import service

wanted = (
    "target:ASFV:blast",
    "target:ASFV:sequences",
    "target:African swine fever virus:blast",
    "target:非洲猪瘟病毒:blast",
    f"target:NC_044959.2:blast",
)
status = {item["key"]: item for item in service.catalog.status()["resources"]}
for key in wanted:
    item = status.get(key)
    assert item and item["available"], f"{key} 不可用: {item}"
PY
do
  if (( SECONDS >= deadline )); then
    printf '[FAIL] agentd 重启后 90 秒内未恢复；正在还原目录配置。\n' >&2
    exit 1
  fi
  sleep 3
done

COMPLETED=true
printf '[PASS] ASFV (NC_044959.2 Georgia 2007/1) 参考文件及 BLAST 数据库已就绪\n'
printf '配置备份：%s\n' "${BACKUP}"