#!/usr/bin/env bash
# =============================================================================
# Register PRRSV2 + PRV reference BLAST databases (one script, two targets)
#   PRRSV2: NC_038291.1 (PRRSV 2, 15,411 bp, RefSeq reference, taxid 1965067)
#   PRV   : NC_006151.1 (Suid alphaherpesvirus 1, 143,461 bp, taxid 10345)
# 说明:
#   - NC_001961.1 是 PRRSV-1(欧洲型 Lelystad), 不要用错
#   - 与 ASFV v2 同模式: 自动探测版本 / inode 稳定写入 / rollback / restart(非recreate)
# 用法: bash register_prrsv2_prv.sh
# 注意: 在服务器宿主机执行(不是容器内); 需要 docker 可访问
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

RUNNING_VERSION="$(agentd_compose exec -T agentd python -c 'import agentd; print(agentd.__version__)' 2>/dev/null | tr -d '\r' || true)"
if [[ -z "${RUNNING_VERSION}" ]]; then
  printf '[STOP] 无法连接 agentd 容器或读取版本; 请在宿主机执行本脚本\n' >&2
  exit 1
fi
printf '[INFO] agentd 运行版本: %s\n' "${RUNNING_VERSION}"

CATALOG="${AGENTD_DEPLOY_DIR}/server-config/database-catalog.json"
BLASTDB_ROOT="$(agentd_resolve_host_path "$(agentd_read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)")"
[[ -f "${CATALOG}" ]] || { printf '[FAIL] missing %s\n' "${CATALOG}" >&2; exit 1; }

BACKUP=""
COMPLETED=false

rollback_catalog() {
  local exit_status=$?
  trap - ERR
  if [[ "${COMPLETED}" != true && -n "${BACKUP}" && -f "${BACKUP}" ]]; then
    printf '[WARN] Registration failed; restoring catalog from %s\n' "${BACKUP}" >&2
    cp -- "${BACKUP}" "${CATALOG}" || true
    agentd_compose restart agentd || true
  fi
  exit "${exit_status}"
}
trap rollback_catalog ERR

# ---------- 逐靶标: 下载 + 校验 + 建库 ----------
TARGETS=(
  "PRRSV2|NC_038291.1|Porcine reproductive and respiratory syndrome virus 2|蓝耳病病毒2型|PRRSV2|prrsv2|Porcine reproductive and respiratory syndrome virus 2"
  "PRV|NC_006151.1|Suid alphaherpesvirus 1|伪狂犬病毒|PRV|prv|Suid herpesvirus 1"
)

for entry in "${TARGETS[@]}"; do
  IFS='|' read -r SHORT ACCESSION FULL_NAME CN_NAME ALIAS1 ALIAS2 ALIAS3 <<< "${entry}"
  printf '\n═══════════ 处理靶标: %s (%s) ═══════════\n' "${FULL_NAME}" "${ACCESSION}"
  REFERENCE_DIR="${BLASTDB_ROOT}/target/$(echo "${ALIAS1}" | tr 'A-Z' 'a-z')/${ACCESSION}"
  REFERENCE_FILE="${REFERENCE_DIR}/source.fasta"
  QUERY_PREFIX="${REFERENCE_DIR}/db"

  install -d -m 755 "${REFERENCE_DIR}"
  if [[ ! -f "${REFERENCE_FILE}" ]]; then
    printf '[INFO] 下载 %s...\n' "${ACCESSION}"
    curl --fail --location --retry 3 --silent --show-error \
      "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=${ACCESSION}&rettype=fasta&retmode=text" \
      -o "${REFERENCE_FILE}.tmp"
    grep -q "^>${ACCESSION}" "${REFERENCE_FILE}.tmp"
    mv "${REFERENCE_FILE}.tmp" "${REFERENCE_FILE}"
  fi

  BASE_COUNT="$(awk '!/^>/{gsub(/[[:space:]]/, ""); total += length($0)} END {print total + 0}' "${REFERENCE_FILE}")"
  # PRRSV2≈15411; PRV≈143461; 统一阈值 140000 可同时校验两者, 也不至于放过残缺
  if (( BASE_COUNT < 140000 )); then
    printf '[STOP] %s 序列不完整（bases=%s）\n' "${ACCESSION}" "${BASE_COUNT}" >&2
    exit 1
  fi
  printf '[INFO] %s 碱基数: %s\n' "${ACCESSION}" "${BASE_COUNT}"

  if ! compgen -G "${QUERY_PREFIX}*.nhr" >/dev/null || \
     ! compgen -G "${QUERY_PREFIX}*.nin" >/dev/null || \
     ! compgen -G "${QUERY_PREFIX}*.nsq" >/dev/null; then
    "${SCRIPT_DIR}/build_blast_db.sh" \
      --input "${REFERENCE_FILE}" \
      --output "${QUERY_PREFIX}" \
      --title "${FULL_NAME} ${ACCESSION}"
    printf '[INFO] BLAST 库已构建: %s\n' "${QUERY_PREFIX}"
  fi

  # 记录到环境变量供 catalog 段使用
  export "REGISTERED_${SHORT}_ACC=${ACCESSION}"
  export "REGISTERED_${SHORT}_DIR=target/$(echo "${ALIAS1}" | tr 'A-Z' 'a-z')/${ACCESSION}"
done

python3 "${SCRIPT_DIR}/validate_catalog.py" \
  --env-file "${AGENTD_ENV_FILE}" \
  --catalog "${CATALOG}" >/dev/null

BACKUP="${CATALOG}.before-prrsv2-prv-$(date +%Y%m%d-%H%M%S).bak"
cp -a -- "${CATALOG}" "${BACKUP}"

# ---------- 更新 catalog（一次性登记两个靶标） ----------
python3 - "${CATALOG}" "${REGISTERED_PRRSV2_ACC}" "${REGISTERED_PRRSV2_DIR}" "${REGISTERED_PRV_ACC}" "${REGISTERED_PRV_DIR}" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
prrsv2_acc, prrsv2_dir = sys.argv[2], sys.argv[3]
prv_acc, prv_dir = sys.argv[4], sys.argv[5]

data = json.loads(path.read_text(encoding="utf-8"))
resources = data.setdefault("resources", {})
if not isinstance(resources, dict):
    raise SystemExit("database-catalog.json 的 resources 必须是对象")

def register(name_list, acc, rel_dir, cn_name):
    blast_resource = {
        "kind": "blast_database",
        "path": f"/data/blastdb/{rel_dir}/db",
        "version": acc,
        "required": False,
    }
    sequence_resource = {
        "kind": "fasta",
        "path": f"/data/blastdb/{rel_dir}/source.fasta",
        "version": acc,
        "required": False,
    }
    for name in name_list:
        resources[f"target:{name}:blast"] = dict(blast_resource)
        resources[f"target:{name}:sequences"] = dict(sequence_resource)

# PRRSV2 别名
register(
    ("PRRSV2", "prrsv2", "Porcine reproductive and respiratory syndrome virus 2",
     "蓝耳病病毒2型", "猪繁殖与呼吸综合征病毒2型", prrsv2_acc),
    prrsv2_acc, prrsv2_dir, "蓝耳病病毒2型",
)
# PRV 别名
register(
    ("PRV", "prv", "Suid alphaherpesvirus 1", "伪狂犬病毒", "Suid herpesvirus 1", prv_acc),
    prv_acc, prv_dir, "伪狂犬病毒",
)

rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
temporary = path.with_name(path.name + ".prrsv2prv.tmp")
with temporary.open("w", encoding="utf-8") as handle:
    handle.write(rendered)
    handle.flush()
    os.fsync(handle.fileno())
with path.open("w", encoding="utf-8") as handle:
    handle.write(rendered)
    handle.flush()
    os.fsync(handle.fileno())
temporary.unlink()
PY
COMPLETED=true

python3 "${SCRIPT_DIR}/validate_catalog.py" \
  --env-file "${AGENTD_ENV_FILE}" \
  --catalog "${CATALOG}" \
  --resource-key 'target:PRRSV2:blast' \
  --resource-key 'target:PRV:blast'

agentd_compose restart agentd

deadline=$((SECONDS + 90))
while ! agentd_compose exec -T agentd python - <<'PY'
from agentd.api import service

wanted = (
    "target:PRRSV2:blast", "target:PRRSV2:sequences",
    "target:Porcine reproductive and respiratory syndrome virus 2:blast",
    "target:蓝耳病病毒2型:blast",
    "target:PRV:blast", "target:PRV:sequences",
    "target:Suid alphaherpesvirus 1:blast",
    "target:伪狂犬病毒:blast",
    "target:NC_038291.1:blast", "target:NC_006151.1:blast",
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
printf '[PASS] PRRSV2 (NC_038291.1) + PRV (NC_006151.1) BLAST 库已登记并可用\n'
printf '配置备份：%s\n' "${BACKUP}"