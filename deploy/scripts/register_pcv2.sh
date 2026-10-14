#!/usr/bin/env bash
# =============================================================================
# 注册 PCV2 (Porcine circovirus 2) 靶标 BLAST 库（自包含, 无服务器依赖）
#   NC_005148.1 (1,768 bp, RefSeq reference complete genome, taxid 85708)
# 注意: PCV2 基因组只有 1768bp -> 碱基阈值用 1500, 不得搬用 ASFV/PRV 的 140000
# 布局: /srv/oligosmith-data/blastdb/target/pcv2/NC_005148.1/
# 用法: bash /tmp/register_pcv2.sh [deploy_dir]
# =============================================================================
set -Eeuo pipefail

DEPLOY_DIR="${1:-}"
if [[ -z "$DEPLOY_DIR" ]]; then
  CAND=()
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do [[ -d "$d" ]] && CAND+=("$d"); done
  [[ ${#CAND[@]} -eq 0 ]] && { echo "[FATAL] 未找到部署目录"; exit 1; }
  DEPLOY_DIR="$(printf '%s\n' "${CAND[@]}" | sort -V | tail -1)"
fi
[[ -d "$DEPLOY_DIR" ]] || { echo "[FATAL] 目录不存在: $DEPLOY_DIR"; exit 1; }
echo "[INFO] 部署目录: $DEPLOY_DIR"

read_env() {
  local key="$1" fallback="${2:-}" value="" env_file="$DEPLOY_DIR/deploy/.env"
  [[ -f "$env_file" ]] && value="$(awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/, ""); print; exit}' "$env_file")"
  value="${value%$'\r'}"; value="${value#\"}"; value="${value%\"}"
  printf '%s\n' "${value:-$fallback}"
}
BLASTDB_ROOT_CONFIG="$(read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)"
if [[ "$BLASTDB_ROOT_CONFIG" == /* ]]; then BLASTDB_ROOT="$BLASTDB_ROOT_CONFIG"; else BLASTDB_ROOT="$DEPLOY_DIR/${BLASTDB_ROOT_CONFIG#./}"; fi
echo "[INFO] 宿主 blastdb 根: $BLASTDB_ROOT"

CATALOG="$DEPLOY_DIR/deploy/server-config/database-catalog.json"
[[ -f "$CATALOG" ]] || { echo "[FAIL] catalog 不存在: $CATALOG"; exit 1; }
COMPOSE=(docker compose --env-file "$DEPLOY_DIR/deploy/.env" -f "$DEPLOY_DIR/deploy/docker-compose.yml")

RUNNING_VERSION="$("${COMPOSE[@]}" exec -T agentd python -c 'import agentd; print(agentd.__version__)' 2>/dev/null | tr -d '\r' || true)"
[[ -z "$RUNNING_VERSION" ]] && { echo "[STOP] 无法连接 agentd 容器"; exit 1; }
echo "[INFO] agentd 运行版本: $RUNNING_VERSION"

IMAGE="$(read_env AGENTD_IMAGE_TAG 1.2.15)"
IMAGE="agentd-primer-probe:${IMAGE}"

BACKUP=""; COMPLETED=false
rollback_catalog() {
  local exit_status=$?
  trap - ERR
  [[ "$COMPLETED" != true && -n "$BACKUP" && -f "$BACKUP" ]] && { echo "[WARN] 失败，还原 catalog"; cp -- "$BACKUP" "$CATALOG" || true; "${COMPOSE[@]}" restart agentd || true; }
  exit "$exit_status"
}
trap rollback_catalog ERR

# ---------- PCV2 ----------
ACCESSION="NC_005148.1"
RELDIR="pcv2"
REF_DIR="$BLASTDB_ROOT/target/$RELDIR/$ACCESSION"
REF_FILE="$REF_DIR/source.fasta"
QUERY="$REF_DIR/db"
mkdir -p "$REF_DIR"

if [[ ! -f "$REF_FILE" ]]; then
  echo "[INFO] 下载 $ACCESSION ..."
  curl --fail --location --retry 3 --silent --show-error \
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=${ACCESSION}&rettype=fasta&retmode=text" \
    -o "${REF_FILE}.tmp"
  grep -q "^>${ACCESSION}" "${REF_FILE}.tmp"
  mv "${REF_FILE}.tmp" "$REF_FILE"
fi

BASE_COUNT="$(awk '!/^>/{gsub(/[[:space:]]/,""); total+=length($0)} END{print total+0}' "$REF_FILE")"
if (( BASE_COUNT < 1500 )); then  # PCV2 全基因组 1768bp, 阈值 1500
  echo "[STOP] $ACCESSION 序列不完整（bases=$BASE_COUNT, 期望≈1768）"; exit 1
fi
echo "[INFO] $ACCESSION 碱基数: $BASE_COUNT（NC_005148.1 = 1,768）"

if ! ls "${QUERY}".nhr "${QUERY}".nin "${QUERY}".nsq >/dev/null 2>&1; then
  # 官方 build_blast_db.sh 同款: --user 当前UID + 子目录挂载
  input_dir="$(cd -- "$(dirname -- "$REF_FILE")" && pwd)"
  input_name="$(basename -- "$REF_FILE")"
  output_parent="$(dirname -- "$QUERY")"
  output_name="$(basename -- "$QUERY")"
  echo "[INFO] 建库: $QUERY（--user $(id -u):$(id -g), 子目录挂载）"
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "${input_dir}:/input:ro" \
    -v "${output_parent}:/output" \
    "${IMAGE}" makeblastdb \
    -in "/input/${input_name}" -dbtype nucl -parse_seqids -title "PCV2 ${ACCESSION}" -out "/output/${output_name}"
  for suffix in .nhr .nin .nsq; do
    if ! ls "${QUERY}${suffix}" >/dev/null 2>&1; then echo "[FAIL] 缺少 ${suffix} 索引"; exit 1; fi
  done
  echo "[INFO] BLAST 库完成: ${QUERY}"
else
  echo "[INFO] BLAST 索引已存在，跳过"
fi

# ---------- catalog ----------
BACKUP="${CATALOG}.before-pcv2-$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CATALOG" "$BACKUP"

python3 - "$CATALOG" <<'PY'
import json, os, sys
ACCESSION = "NC_005148.1"
RELDIR = "pcv2"
path = sys.argv[1]
data = json.loads(open(path, encoding="utf-8").read())
resources = data.setdefault("resources", {})
if not isinstance(resources, dict):
    raise SystemExit("resources 必须是对象")

names = ("PCV2", "pcv2", "Porcine circovirus 2", "猪圆环病毒2型", "猪圆环病毒2", ACCESSION)
for name in names:
    resources[f"target:{name}:blast"] = {"kind": "blast_database", "path": f"/data/blastdb/target/{RELDIR}/{ACCESSION}/db", "version": ACCESSION, "required": False}
    resources[f"target:{name}:sequences"] = {"kind": "fasta", "path": f"/data/blastdb/target/{RELDIR}/{ACCESSION}/source.fasta", "version": ACCESSION, "required": False}

rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
tmp = path + ".pcv2.tmp"
open(tmp, "w", encoding="utf-8").write(rendered); os.fsync(os.open(tmp, os.O_WRONLY))
open(path, "w", encoding="utf-8").write(rendered); os.fsync(os.open(path, os.O_WRONLY))
os.unlink(tmp)
PY
COMPLETED=true
echo "[INFO] catalog 已更新"

# ---------- 重启 + 验证 ----------
"${COMPOSE[@]}" restart agentd
deadline=$((SECONDS + 90))
while ! "${COMPOSE[@]}" exec -T agentd python -c "
from agentd.api import service
wanted = ('target:PCV2:blast','target:PCV2:sequences','target:Porcine circovirus 2:blast','target:猪圆环病毒2型:blast','target:NC_005148.1:blast')
status = {item['key']: item for item in service.catalog.status()['resources']}
for key in wanted:
    item = status.get(key)
    assert item and item['available'], f'{key} 不可用: {item}'
" 2>/dev/null; do
  if (( SECONDS >= deadline )); then echo "[FAIL] 90秒未恢复"; exit 1; fi
  sleep 3
done

COMPLETED=true
echo "[PASS] PCV2 (NC_005148.1) BLAST 库已登记并可用"
echo "备份: $BACKUP"
