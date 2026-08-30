#!/usr/bin/env bash
# Register the ASFV NC_001659.2 reference without recreating the Compose
# container.  The catalog is updated in place because it is mounted as a
# single bind-mounted file; replacing that file would leave an existing
# container attached to the old inode.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

EXPECTED_VERSION="1.2.13"
CATALOG="${AGENTD_DEPLOY_DIR}/server-config/database-catalog.json"
BLASTDB_ROOT="$(agentd_resolve_host_path "$(agentd_read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)")"
REFERENCE_DIR="${ASFV_REFERENCE_DIR:-${BLASTDB_ROOT}/target/asfv/NC_001659.2}"
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

RUNNING_VERSION="$(agentd_compose exec -T agentd python -c 'import agentd; print(agentd.__version__)' | tr -d '\r')"
if [[ "${RUNNING_VERSION}" != "${EXPECTED_VERSION}" ]]; then
  printf '[STOP] 当前运行版本不是 %s：%s\n' "${EXPECTED_VERSION}" "${RUNNING_VERSION}" >&2
  exit 1
fi

install -d -m 755 "${REFERENCE_DIR}"
if [[ ! -f "${REFERENCE_FILE}" ]]; then
  curl --fail --location --retry 3 --silent --show-error \
    'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=NC_001659.2&rettype=fasta&retmode=text' \
    -o "${REFERENCE_FILE}.tmp"
  grep -q '^>NC_001659.2' "${REFERENCE_FILE}.tmp"
  mv "${REFERENCE_FILE}.tmp" "${REFERENCE_FILE}"
fi

BASE_COUNT="$(awk '!/^>/{gsub(/[[:space:]]/, ""); total += length($0)} END {print total + 0}' "${REFERENCE_FILE}")"
if (( BASE_COUNT < 150000 )); then
  printf '[STOP] 下载的 ASFV 参考文件不完整（bases=%s）\n' "${BASE_COUNT}" >&2
  exit 1
fi

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
    --title 'ASFV NC_001659.2'
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

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
resources = data.setdefault("resources", {})
if not isinstance(resources, dict):
    raise SystemExit("database-catalog.json 的 resources 必须是对象")

names = (
    "ASFV",
    "asfv",
    "African swine fever virus",
    "非洲猪瘟病毒",
    "非洲猪瘟",
    "NC_001659.2",
)
blast_resource = {
    "kind": "blast_database",
    "path": "/data/blastdb/target/asfv/NC_001659.2/db",
    "version": "NC_001659.2",
    "required": False,
}
sequence_resource = {
    "kind": "fasta",
    "path": "/data/blastdb/target/asfv/NC_001659.2/source.fasta",
    "version": "NC_001659.2",
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

# Deliberately preserve the catalog file inode; see the comment above.
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

# A restart only interrupts agentd briefly; unlike `up --force-recreate`, it
# does not replace the container, its network endpoint, or the mounted-file
# inode.
agentd_compose restart agentd

deadline=$((SECONDS + 90))
while ! agentd_compose exec -T agentd python - <<'PY'
from agentd.api import service

wanted = (
    "target:ASFV:blast",
    "target:ASFV:sequences",
    "target:African swine fever virus:blast",
    "target:非洲猪瘟病毒:blast",
    "target:NC_001659.2:blast",
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
printf '[PASS] ASFV 参考文件及 BLAST 数据库已经准备完成\n'
printf '配置备份：%s\n' "${BACKUP}"
