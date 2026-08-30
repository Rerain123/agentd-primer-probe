#!/usr/bin/env bash
# =============================================================================
# 自包含: 注册 PRRSV2 + PRV 两个靶标 BLAST 库（不依赖服务器辅助脚本）
#   PRRSV2: NC_038291.1 (PRRSV 2, 15,411 bp, RefSeq reference, taxid 1965067)
#   PRV   : NC_006151.1 (Suid alphaherpesvirus 1, 143,461 bp, taxid 10345)
# 注意: NC_001961.1 是 PRRSV-1(欧洲型), 勿混用
# 用法: bash /tmp/register_prrsv2_prv.sh [deploy_dir]
# =============================================================================
set -Eeuo pipefail

# ---------- 定位部署目录 ----------
DEPLOY_DIR="${1:-}"
if [[ -z "$DEPLOY_DIR" ]]; then
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do
    [[ -d "$d" ]] && DEPLOY_DIR="$d" && break
  done
fi
[[ -z "$DEPLOY_DIR" || ! -d "$DEPLOY_DIR" ]] && { echo "[FATAL] 找不到部署目录: bash $0 /path/to/deploy"; exit 1; }
echo "[INFO] 部署目录: $DEPLOY_DIR"

# ---------- 解析 env 中 BLASTDB 宿主路径（兼容相对/绝对） ----------
read_env() { # $1=key $2=fallback
  local key="$1" fallback="${2:-}" value="" env_file="$DEPLOY_DIR/deploy/.env"
  [[ -f "$env_file" ]] && value="$(awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/, ""); print; exit}' "$env_file")"
  value="${value%$'\r'}"; value="${value#\"}"; value="${value%\"}"
  printf '%s\n' "${value:-$fallback}"
}
BLASTDB_ROOT_CONFIG="$(read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)"
if [[ "$BLASTDB_ROOT_CONFIG" == /* ]]; then
  BLASTDB_ROOT="$BLASTDB_ROOT_CONFIG"
else
  BLASTDB_ROOT="$DEPLOY_DIR/${BLASTDB_ROOT_CONFIG#./}"
fi
CONTAINER_ROOT="${BLASTDB_ROOT//\/srv\/oligosmith-data\/blastdb/\/data\/blastdb}"
CONTAINER_ROOT="${CONTAINER_ROOT//\/srv\/agentd-data\/blastdb/\/data\/blastdb}"
echo "[INFO] 宿主 blastdb: $BLASTDB_ROOT"
echo "[INFO] 容器 blastdb: $CONTAINER_ROOT"

CATALOG="$DEPLOY_DIR/deploy/server-config/database-catalog.json"
[[ -f "$CATALOG" ]] || { echo "[FAIL] catalog 不存在: $CATALOG"; exit 1; }
COMPOSE=(docker compose --env-file "$DEPLOY_DIR/deploy/.env" -f "$DEPLOY_DIR/deploy/docker-compose.yml")

# 版本自动探测（不硬编码）
RUNNING_VERSION="$("${COMPOSE[@]}" exec -T agentd python -c 'import agentd; print(agentd.__version__)' 2>/dev/null | tr -d '\r' || true)"
[[ -z "$RUNNING_VERSION" ]] && { echo "[STOP] 无法连接 agentd 容器（请在宿主机执行）"; exit 1; }
echo "[INFO] agentd 运行版本: $RUNNING_VERSION"

BACKUP=""; COMPLETED=false
rollback_catalog() {
  local exit_status=$?
  trap - ERR
  if [[ "$COMPLETED" != true && -n "$BACKUP" && -f "$BACKUP" ]]; then
    echo "[WARN] 注册失败，还原 catalog（$BACKUP）"
    cp -- "$BACKUP" "$CATALOG" || true
    "${COMPOSE[@]}" restart agentd || true
  fi
  exit "$exit_status"
}
trap rollback_catalog ERR

# 容器内执行命令（宿主无 makeblastdb 时兜底）
run_in_container() { "${COMPOSE[@]}" run --rm -v "$BLASTDB_ROOT:$CONTAINER_ROOT:rw" agentd bash -c "$1"; }
if command -v makeblastdb >/dev/null 2>&1; then
  BUILD_IN=host
else
  BUILD_IN=container
  echo "[INFO] 宿主无 makeblastdb，改在容器内建库"
fi

# ---------- 逐靶标处理 ----------
declare -A REGISTERED_ACC REGISTERED_DIR
TARGETS=(
  "PRRSV2|NC_038291.1|Porcine reproductive and respiratory syndrome virus 2|prrsv2"
  "PRV|NC_006151.1|Suid alphaherpesvirus 1|prv"
)
for entry in "${TARGETS[@]}"; do
  IFS='|' read -r SHORT ACCESSION FULL_NAME RELDIR <<< "$entry"
  echo ""
  echo "═══════════ 处理靶标: $FULL_NAME ($ACCESSION) ═══════════"
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
  if (( BASE_COUNT < 140000 )); then
    echo "[STOP] $ACCESSION 序列不完整（bases=$BASE_COUNT）"; exit 1
  fi
  echo "[INFO] $ACCESSION 碱基数: $BASE_COUNT"

  if ! ls "${QUERY}".nhr "${QUERY}".nin "${QUERY}".nsq >/dev/null 2>&1; then
    if [[ "$BUILD_IN" == host ]]; then
      makeblastdb -in "$REF_FILE" -dbtype nucl -out "$QUERY" -title "$FULL_NAME $ACCESSION" -parse_seqids
    else
      run_in_container "makeblastdb -in \"$CONTAINER_ROOT/target/$RELDIR/$ACCESSION/source.fasta\" -dbtype nucl -out \"$CONTAINER_ROOT/target/$RELDIR/$ACCESSION/db\" -title '$FULL_NAME $ACCESSION' -parse_seqids"
    fi
    echo "[INFO] BLAST 库已构建: $QUERY"
  fi

  REGISTERED_ACC["$SHORT"]="$ACCESSION"
  REGISTERED_DIR["$SHORT"]="target/$RELDIR/$ACCESSION"
done

# ---------- 更新 catalog ----------
BACKUP="${CATALOG}.before-prrsv2-prv-$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CATALOG" "$BACKUP"

P_ACC1="${REGISTERED_ACC[PRRSV2]}"; P_DIR1="${REGISTERED_DIR[PRRSV2]}"
P_ACC2="${REGISTERED_ACC[PRV]}";    P_DIR2="${REGISTERED_DIR[PRV]}"
python3 - "$CATALOG" "$P_ACC1" "$P_DIR1" "$P_ACC2" "$P_DIR2" <<'PY'
import json, os, sys
from pathlib import Path

path = Path(sys.argv[1])
prrsv2_acc, prrsv2_dir = sys.argv[2], sys.argv[3]
prv_acc, prv_dir = sys.argv[4], sys.argv[5]

data = json.loads(path.read_text(encoding="utf-8"))
resources = data.setdefault("resources", {})
if not isinstance(resources, dict):
    raise SystemExit("resources 必须是对象")

def register(name_list, acc, rel_dir):
    blast = {"kind": "blast_database", "path": f"/data/blastdb/{rel_dir}/db", "version": acc, "required": False}
    seq = {"kind": "fasta", "path": f"/data/blastdb/{rel_dir}/source.fasta", "version": acc, "required": False}
    for name in name_list:
        resources[f"target:{name}:blast"] = dict(blast)
        resources[f"target:{name}:sequences"] = dict(seq)

register(("PRRSV2", "prrsv2", "Porcine reproductive and respiratory syndrome virus 2",
          "蓝耳病病毒2型", "猪繁殖与呼吸综合征病毒2型", prrsv2_acc), prrsv2_acc, prrsv2_dir)
register(("PRV", "prv", "Suid alphaherpesvirus 1", "伪狂犬病毒", "Suid herpesvirus 1", prv_acc), prv_acc, prv_dir)

rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
tmp = path.with_name(path.name + ".pp.tmp")
with tmp.open("w", encoding="utf-8") as h:
    h.write(rendered); h.flush(); os.fsync(h.fileno())
with path.open("w", encoding="utf-8") as h:
    h.write(rendered); h.flush(); os.fsync(h.fileno())
tmp.unlink()
PY
COMPLETED=true
echo "[INFO] catalog 已更新"

# ---------- 重启并验证 ----------
"${COMPOSE[@]}" restart agentd
deadline=$((SECONDS + 90))
while ! "${COMPOSE[@]}" exec -T agentd python -c "
from agentd.api import service
wanted = ('target:PRRSV2:blast','target:PRRSV2:sequences','target:Porcine reproductive and respiratory syndrome virus 2:blast','target:蓝耳病病毒2型:blast','target:PRV:blast','target:PRV:sequences','target:Suid alphaherpesvirus 1:blast','target:伪狂犬病毒:blast','target:NC_038291.1:blast','target:NC_006151.1:blast')
status = {item['key']: item for item in service.catalog.status()['resources']}
for key in wanted:
    item = status.get(key)
    assert item and item['available'], f'{key} 不可用: {item}'
" 2>/dev/null; do
  if (( SECONDS >= deadline )); then echo "[FAIL] 90秒未恢复，已尝试回滚"; exit 1; fi
  sleep 3
done

COMPLETED=true
echo "[PASS] PRRSV2 (NC_038291.1) + PRV (NC_006151.1) BLAST 库已登记并可用"
echo "备份: $BACKUP"