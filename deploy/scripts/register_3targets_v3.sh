#!/usr/bin/env bash
# =============================================================================
# 合并注册 ASFV + PRRSV2 + PRV 三个靶标 BLAST 库 (v3 修复版)
# v3 修复: 建库改用官方 build_blast_db.sh 同款方式——
#   docker run --user $(id -u):$(id -g) + 子目录挂载(/input:ro, /output 可写)
#   绕开 compose 服务中 /data/blastdb 只读卷冲突（之前失败根因）
# 布局: /srv/oligosmith-data/blastdb/target/<name>/<accession>/
#   ASFV   : NC_044959.2 (Georgia 2007/1, 190,584 bp)
#   PRRSV2 : NC_038291.1 (PRRSV 2, 15,411 bp)
#   PRV    : NC_006151.1 (Suid alphaherpesvirus 1, 143,461 bp)
# 用法: bash /tmp/register_3targets_v3.sh [deploy_dir]
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

# 镜像名（与 build_blast_db.sh 的 agentd_image_name 同逻辑）
IMAGE="$(read_env AGENTD_IMAGE_TAG 1.2.13)"
IMAGE="agentd-primer-probe:${IMAGE}"
echo "[INFO] 构建镜像: $IMAGE"

BACKUP=""; COMPLETED=false
rollback_catalog() {
  local exit_status=$?
  trap - ERR
  [[ "$COMPLETED" != true && -n "$BACKUP" && -f "$BACKUP" ]] && { echo "[WARN] 失败，还原 catalog"; cp -- "$BACKUP" "$CATALOG" || true; "${COMPOSE[@]}" restart agentd || true; }
  exit "$exit_status"
}
trap rollback_catalog ERR

# 建库（官方 build_blast_db.sh 同款: --user 当前UID + 子目录挂载, 避开只读卷冲突）
build_db() { # $1=input(fasta) $2=output(prefix)
  local input="$1" output="$2"
  local input_dir input_name output_parent output_name
  input_dir="$(cd -- "$(dirname -- "$input")" && pwd)"
  input_name="$(basename -- "$input")"
  output_parent="$(dirname -- "$output")"
  output_name="$(basename -- "$output")"
  mkdir -p -- "$output_parent"
  [[ -f "$input" ]] || { echo "[FAIL] FASTA 不存在: $input"; exit 1; }
  echo "[INFO] 建库: $output（--user $(id -u):$(id -g), 子目录挂载）"
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "${input_dir}:/input:ro" \
    -v "${output_parent}:/output" \
    "${IMAGE}" makeblastdb \
    -in "/input/${input_name}" -dbtype nucl -parse_seqids -title "$(basename -- "$output_parent")" -out "/output/${output_name}"
  for suffix in .nhr .nin .nsq; do
    if ! ls "${output}${suffix}" >/dev/null 2>&1; then
      echo "[FAIL] 建库后缺少 ${suffix} 索引"; exit 1
    fi
  done
  echo "[INFO] BLAST 库完成: ${output}"
}

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
  if (( BASE_COUNT < MIN_BASES )); then echo "[STOP] $ACCESSION 不完整（bases=$BASE_COUNT）"; exit 1; fi
  echo "[INFO] $ACCESSION 碱基数: $BASE_COUNT"

  if ! ls "${QUERY}".nhr "${QUERY}".nin "${QUERY}".nsq >/dev/null 2>&1; then
    build_db "$REF_FILE" "$QUERY"
  else
    echo "[INFO] BLAST 索引已存在，跳过"
  fi

  REG_ACC["$SHORT"]="$ACCESSION"
  REG_DIR["$SHORT"]="target/$RELDIR/$ACCESSION"
done

# ---------- catalog ----------
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
echo "[INFO] catalog 已更新"

"${COMPOSE[@]}" restart agentd
deadline=$((SECONDS + 90))
while ! "${COMPOSE[@]}" exec -T agentd python -c "
from agentd.api import service
wanted = ('target:ASFV:blast','target:ASFV:sequences','target:African swine fever virus:blast','target:非洲猪瘟病毒:blast','target:NC_044959.2:blast','target:PRRSV2:blast','target:PRRSV2:sequences','target:PRV:blast','target:PRV:sequences','target:Suid alphaherpesvirus 1:blast','target:伪狂犬病毒:blast','target:NC_038291.1:blast','target:NC_006151.1:blast')
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
