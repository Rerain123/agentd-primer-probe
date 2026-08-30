#!/usr/bin/env bash
# =============================================================================
# 合并注册 ASFV + PRRSV2 + PRV 三个靶标 BLAST 库（完全自包含, 无服务器依赖）
# 布局与现有库对齐: /srv/oligosmith-data/blastdb/target/<name>/<accession>/
#   ASFV   : NC_044959.2 (Georgia 2007/1, 190,584 bp, RefSeq reference)
#   PRRSV2 : NC_038291.1 (PRRSV 2, 15,411 bp, RefSeq reference)
#   PRV    : NC_006151.1 (Suid alphaherpesvirus 1, 143,461 bp)
# 注意:
#   - NC_001961.1 是 PRRSV-1(欧洲型), 勿用
#   - 每个靶标独立碱基阈值(ASFV 190k / PRRSV2 15k / PRV 143k), 防止误判
# 用法: bash /tmp/register_3targets.sh [deploy_dir]
# =============================================================================
set -Eeuo pipefail

DEPLOY_DIR="${1:-}"
if [[ -z "$DEPLOY_DIR" ]]; then
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do
    [[ -d "$d" ]] && DEPLOY_DIR="$d" && break
  done
fi
[[ -z "$DEPLOY_DIR" || ! -d "$DEPLOY_DIR" ]] && { echo "[FATAL] 找不到部署目录: bash $0 /path/to/deploy"; exit 1; }
echo "[INFO] 部署目录: $DEPLOY_DIR"

read_env() {
  local key="$1" fallback="${2:-}" value="" env_file="$DEPLOY_DIR/deploy/.env"
  [[ -f "$env_file" ]] && value="$(awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/, ""); print; exit}' "$env_file")"
  value="${value%$'\r'}"; value="${value#\"}"; value="${value%\"}"
  printf '%s\n' "${value:-$fallback}"
}
BLASTDB_ROOT_CONFIG="$(read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)"
if [[ "$BLASTDB_ROOT_CONFIG" == /* ]]; then BLASTDB_ROOT="$BLASTDB_ROOT_CONFIG"; else BLASTDB_ROOT="$DEPLOY_DIR/${BLASTDB_ROOT_CONFIG#./}"; fi
CONTAINER_ROOT="${BLASTDB_ROOT/\/srv\/oligosmith-data\/blastdb/\/data\/blastdb}"
CONTAINER_ROOT="${CONTAINER_ROOT/\/srv\/agentd-data\/blastdb/\/data\/blastdb}"
echo "[INFO] 宿主 blastdb: $BLASTDB_ROOT"
echo "[INFO] 容器 blastdb: $CONTAINER_ROOT"

CATALOG="$DEPLOY_DIR/deploy/server-config/database-catalog.json"
[[ -f "$CATALOG" ]] || { echo "[FAIL] catalog 不存在: $CATALOG"; exit 1; }
COMPOSE=(docker compose --env-file "$DEPLOY_DIR/deploy/.env" -f "$DEPLOY_DIR/deploy/docker-compose.yml")

RUNNING_VERSION="$("${COMPOSE[@]}" exec -T agentd python -c 'import agentd; print(agentd.__version__)' 2>/dev/null | tr -d '\r' || true)"
[[ -z "$RUNNING_VERSION" ]] && { echo "[STOP] 无法连接 agentd 容器（请在宿主机执行）"; exit 1; }
echo "[INFO] agentd 运行版本: $RUNNING_VERSION"

BACKUP=""; COMPLETED=false
rollback_catalog() {
  local exit_status=$?
  trap - ERR
  [[ "$COMPLETED" != true && -n "$BACKUP" && -f "$BACKUP" ]] && { echo "[WARN] 失败，还原 catalog"; cp -- "$BACKUP" "$CATALOG" || true; "${COMPOSE[@]}" restart agentd || true; }
  exit "$exit_status"
}
trap rollback_catalog ERR

if command -v makeblastdb >/dev/null 2>&1; then BUILD_IN=host; else BUILD_IN=container; echo "[INFO] 宿主无 makeblastdb，改用容器内建库"; fi

# 靶标定义: 简称|accession|全名|rel目录|最小碱基数
TARGETS=(
  "ASFV|NC_044959.2|African swine fever virus|asfv|180000"
  "PRRSV2|NC_038291.1|Porcine reproductive and respiratory syndrome virus 2|prrsv2|14000"
  "PRV|NC_006151.1|Suid alphaherpesvirus 1|prv|140000"
)
declare -A REG_ACC REG_DIR

for entry in "${TARGETS[@]}"; do
  IFS='|' read -r SHORT ACCESSION FULL_NAME RELDIR MIN_BASES <<< "$entry"
  echo ""
  echo "═══════════ $FULL_NAME ($ACCESSION) ═══════════"
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
  if (( BASE_COUNT < MIN_BASES )); then
    echo "[STOP] $ACCESSION 序列不完整（bases=$BASE_COUNT, 期望≥$MIN_BASES）"; exit 1
  fi
  echo "[INFO] $ACCESSION 碱基数: $BASE_COUNT（校验阈值 $MIN_BASES）"

  if ! ls "${QUERY}".nhr "${QUERY}".nin "${QUERY}".nsq >/dev/null 2>&1; then
    if [[ "$BUILD_IN" == host ]]; then
      makeblastdb -in "$REF_FILE" -dbtype nucl -out "$QUERY" -title "$FULL_NAME $ACCESSION" -parse_seqids
    else
      "${COMPOSE[@]}" run --rm -v "$BLASTDB_ROOT:$CONTAINER_ROOT:rw" agentd \
        bash -c "makeblastdb -in '$CONTAINER_ROOT/target/$RELDIR/$ACCESSION/source.fasta' -dbtype nucl -out '$CONTAINER_ROOT/target/$RELDIR/$ACCESSION/db' -title '$FULL_NAME $ACCESSION' -parse_seqids"
    fi
    echo "[INFO] BLAST 库已构建: $QUERY"
  else
    echo "[INFO] BLAST 索引已存在，跳过构建"
  fi

  REG_ACC["$SHORT"]="$ACCESSION"
  REG_DIR["$SHORT"]="target/$RELDIR/$ACCESSION"
done

# ---------- catalog 更新 ----------
BACKUP="${CATALOG}.before-3targets-$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CATALOG" "$BACKUP"

python3 - "$CATALOG" \
  "${REG_ACC[ASFV]}"   "${REG_DIR[ASFV]}" \
  "${REG_ACC[PRRSV2]}" "${REG_DIR[PRRSV2]}" \
  "${REG_ACC[PRV]}"    "${REG_DIR[PRV]}" <<'PY'
import json, os, sys

path = sys.argv[1]
asfv_acc, asfv_dir = sys.argv[2], sys.argv[3]
prrsv2_acc, prrsv2_dir = sys.argv[4], sys.argv[5]
prv_acc, prv_dir = sys.argv[6], sys.argv[7]

data = json.loads(open(path, encoding="utf-8").read())
resources = data.setdefault("resources", {})
if not isinstance(resources, dict):
    raise SystemExit("resources 必须是对象")

def register(name_list, acc, rel_dir):
    for name in name_list:
        resources[f"target:{name}:blast"] = {"kind": "blast_database", "path": f"/data/blastdb/{rel_dir}/db", "version": acc, "required": False}
        resources[f"target:{name}:sequences"] = {"kind": "fasta", "path": f"/data/blastdb/{rel_dir}/source.fasta", "version": acc, "required": False}

# ASFV（清理旧 NC_001659 残留）
for key in list(resources.keys()):
    if "NC_001659" in key or (isinstance(resources.get(key), dict) and "NC_001659" in str(resources[key].get("version", ""))):
        del resources[key]
        print(f"[INFO] 移除旧 ASFV 条目: {key}", flush=True)
register(("ASFV","asfv","African swine fever virus","非洲猪瘟病毒","非洲猪瘟",asfv_acc), asfv_acc, asfv_dir)

register(("PRRSV2","prrsv2","Porcine reproductive and respiratory syndrome virus 2","蓝耳病病毒2型","猪繁殖与呼吸综合征病毒2型",prrsv2_acc), prrsv2_acc, prrsv2_dir)
register(("PRV","prv","Suid alphaherpesvirus 1","伪狂犬病毒","Suid herpesvirus 1",prv_acc), prv_acc, prv_dir)

rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
tmp = path + ".3t.tmp"
open(tmp, "w", encoding="utf-8").write(rendered); os.fsync(os.open(tmp, os.O_WRONLY))
open(path, "w", encoding="utf-8").write(rendered); os.fsync(os.open(path, os.O_WRONLY))
os.unlink(tmp)
PY
COMPLETED=true
echo "[INFO] catalog 已更新（含旧 NC_001659 清理）"

# ---------- 重启 + 验证 ----------
"${COMPOSE[@]}" restart agentd
deadline=$((SECONDS + 90))
while ! "${COMPOSE[@]}" exec -T agentd python -c "
from agentd.api import service
wanted = ('target:ASFV:blast','target:ASFV:sequences','target:African swine fever virus:blast','target:非洲猪瘟病毒:blast','target:NC_044959.2:blast','target:PRRSV2:blast','target:PRRSV2:sequences','target:Blue-ear Disease Virus 2:blast' if 'Blue-ear Disease Virus 2:blast' in [x['key'] for x in service.catalog.status()['resources']] else 'target:PRRSV2:blast','target:PRV:blast','target:PRV:sequences','target:Suid alphaherpesvirus 1:blast','target:伪狂犬病毒:blast','target:NC_038291.1:blast','target:NC_006151.1:blast')
status = {item['key']: item for item in service.catalog.status()['resources']}
for key in wanted:
    item = status.get(key)
    assert item and item['available'], f'{key} 不可用: {item}'
" 2>/dev/null; do
  if (( SECONDS >= deadline )); then echo "[FAIL] 90秒未恢复"; exit 1; fi
  sleep 3
done

COMPLETED=true
echo "[PASS] ASFV (NC_044959.2) + PRRSV2 (NC_038291.1) + PRV (NC_006151.1) 全部登记并可用"
echo "备份: $BACKUP"