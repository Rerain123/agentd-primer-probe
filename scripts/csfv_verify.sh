#!/usr/bin/env bash
# =============================================================================
# CSFV 靶标专库 + MAFFT 多序列比对 服务器验证脚本
# 用法:
#   bash csfv_verify.sh            # 自动探测（推荐）
#   bash csfv_verify.sh /opt/oligosmith/agentd-primer-probe-server-1.1.2
# 环境要求: 在服务器宿主机上运行; 需要 docker 可访问（容器内已有 blastn/mafft）
# 说明: 若服务器本地也装了 blast+/mafft，脚本会优先直接用宿主命令。
# =============================================================================
set -uo pipefail

PASS=0; FAIL=0
ok()   { echo -e "  \033[32m[PASS]\033[0m $*"; PASS=$((PASS+1)); }
bad()  { echo -e "  \033[31m[FAIL]\033[0m $*"; FAIL=$((FAIL+1)); }
info() { echo -e "  \033[36m[INFO]\033[0m $*"; }

echo "==================== CSFV 库验证 ===================="

# ---------- 0. 定位部署目录 ----------
DEPLOY_DIR="${1:-}"
if [[ -z "$DEPLOY_DIR" ]]; then
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do
    [[ -d "$d" ]] && DEPLOY_DIR="$d" && break
  done
fi
[[ -z "$DEPLOY_DIR" || ! -d "$DEPLOY_DIR" ]] && { echo "[FATAL] 找不到部署目录，请用参数指定: bash $0 /path/to/deploy"; exit 1; }
info "部署目录: $DEPLOY_DIR"
COMPOSE=(docker compose --env-file "$DEPLOY_DIR/deploy/.env" -f "$DEPLOY_DIR/deploy/docker-compose.yml")

# ---------- 1. 定位 CSFV BLAST 库 ----------
info "定位 CSFV blastdb 目录 ..."
CSFV_DIR="$(find /srv -maxdepth 6 -type d -iname "*csfv*" 2>/dev/null | head -1)"
if [[ -z "$CSFV_DIR" ]]; then
  # 兜底: 在容器内 /data/blastdb 下找
  CSFV_DIR="$("${COMPOSE[@]}" exec -T agentd bash -c 'find /data/blastdb -maxdepth 4 -type d -iname "*csfv*" 2>/dev/null | head -1' | tr -d '\r')"
fi
if [[ -z "$CSFV_DIR" ]]; then
  bad "未找到 CSFV 库目录（已扫 /srv 与容器内 /data/blastdb）"
  exit 1
fi
info "CSFV 库目录: $CSFV_DIR"

DB_PREFIX="$CSFV_DIR/db"
CONTAINER_PREFIX="${DB_PREFIX#/srv/oligosmith-data/blastdb}"   # 若宿主在 /srv/oligosmith-data
CONTAINER_PREFIX="${CONTAINER_PREFIX#/srv/agentd-data/blastdb}" # 或 /srv/agentd-data

# ---------- 2. BLAST 索引文件三件套 ----------
info "检查索引文件 (.nhr/.nin/.nsq) ..."
if ls "${DB_PREFIX}".nhr "${DB_PREFIX}".nin "${DB_PREFIX}".nsq >/dev/null 2>&1; then
  ok "索引三件套存在: ${DB_PREFIX}.*"
else
  bad "索引文件缺失: ${DB_PREFIX}.{nhr,nin,nsq}"
fi

# ---------- 3. 执行方式选择 (宿主机优先，否则容器内) ----------
run_in_container() { "${COMPOSE[@]}" exec -T agentd bash -c "$1"; }
if command -v blastdbcmd >/dev/null 2>&1; then
  RUN=host;   BLASTDB_CMD="blastdbcmd"; BLASTN_CMD="blastn"; MAFFT_CMD="mafft"
  RUN_PREFIX="$DB_PREFIX"
  info "使用宿主 blast+ 执行"
else
  RUN=container
  BLASTDB_CMD="blastdbcmd"; BLASTN_CMD="blastn"; MAFFT_CMD="mafft"
  RUN_PREFIX="$CONTAINER_PREFIX"
  info "宿主无 blast+，改在容器内执行（prefix=$RUN_PREFIX）"
fi

exec_blast() { # $1=命令体
  if [[ "$RUN" == host ]]; then bash -c "$1"; else "${COMPOSE[@]}" exec -T agentd bash -c "$1"; fi
}

# ---------- 4. blastdbcmd -info: 索引可读性 + 库规模 ----------
echo "--- 4. blastdbcmd -info ---"
OUT="$(exec_blast "$BLASTDB_CMD -db '$RUN_PREFIX' -info 2>&1")"
if [[ "$?" -eq 0 && "$OUT" == *"sequences"* ]]; then
  ok "blastdbcmd 可读取索引"
  echo "$OUT" | sed 's/^/      /'
  BLAST_LEN="$(echo "$OUT" | grep -oE 'Number of letters in database: [0-9,]+' | grep -oE '[0-9,]+')"
  [[ -n "$BLAST_LEN" ]] && info "库碱基数: $BLAST_LEN（CSFV 单参考基因组应约 12,300）"
else
  bad "blastdbcmd 读取失败: $OUT"
fi

# ---------- 5. 真实 blastn 比对（CSFV 5'UTR 查询自健） ----------
echo "--- 5. blastn 真实比对 (5'UTR 片段查库) ---"
cat > /tmp/csfv_q.fa <<'EOF'
>q_csfv_5utr
GTATACGAGGTTAGTTCATTCTCGTATGCATGATTGGACAAATCAAAATTTCAATTTGGTTCAGGGCCTCCCTCCAGCGACGGCCGAACTGGGCTAGCCATGCCCACAGTAGGACTAGCAAACGGAGGGACTAGCCGTAGTGGCGAGCTCCCTGGGTGGTCTAAGTCCTGAGTACAGGACAGTCGTCAGTAGTTCGACGTGAGCAGAAGCCCACCTCGAGATGCTATGTGGACGAGGGCATGCCCAAGACACACCTTAACCCTAGCGGGGGTCGCTAGGGTGAAATCACACCACGTGATG
EOF
QUERY="/tmp/csfv_q.fa"
if [[ "$RUN" == container ]]; then
  "${COMPOSE[@]}" cp /tmp/csfv_q.fa agentd:/tmp/csfv_q.fa 2>/dev/null || true
  QUERY="/tmp/csfv_q.fa"
fi
HITS="$(exec_blast "$BLASTN_CMD -db '$RUN_PREFIX' -query '$QUERY' -outfmt '6 qseqid sseqid pident length evalue bitscore' 2>&1")"
if [[ "$?" -eq 0 && -n "$HITS" ]]; then
  ok "blastn 比对成功，命中 $(echo "$HITS" | wc -l) 条:"
  echo "$HITS" | sed 's/^/      /'
else
  bad "blastn 无命中或失败: $HITS"
fi

# ---------- 6. MAFFT 多序列比对（内嵌 4 条真实 5'UTR 300nt） ----------
echo "--- 6. MAFFT 多序列比对 (4 条 CSFV 5'UTR) ---"
cat > /tmp/csfv_msa.fa <<'EOF'
>NC_002657.1
GTATACGAGGTTAGTTCATTCTCGTATGCATGATTGGACAAATCAAAATTTCAATTTGGTTCAGGGCCTCCCTCCAGCGACGGCCGAACTGGGCTAGCCATGCCCACAGTAGGACTAGCAAACGGAGGGACTAGCCGTAGTGGCGAGCTCCCTGGGTGGTCTAAGTCCTGAGTACAGGACAGTCGTCAGTAGTTCGACGTGAGCAGAAGCCCACCTCGAGATGCTATGTGGACGAGGGCATGCCCAAGACACACCTTAACCCTAGCGGGGGTCGCTAGGGTGAAATCACACCACGTGATG
>AF091661.1
GTATACGAGGTTAGTTCATTCTCGTATGCATGATTGGACAAATCAAAATTTCAATTTGGTTCAGGGCCCCCCTCCAGCGACGGCCGAACTGGGCTAGCCATGCCCACAGTAGGACTAGCAAACGGAGGGACTAGCCGTAGTGGCGAGCTCCCTGGGTGGTCTAAGTCCTGAGTACAGGACAGTCGTCAGTAGTTCGACGTGAGCAGAAGCCCACCTCGAGATGCTACGTGGACGAGGGCATGCCCAAGACACACCTTAACTCTAGCGGGGGTCGCTAGGGTGAAATCACACCACGTGATG
>AF531433.1
GTATACGAGGTTAGTTCATTCTCGTATACACGATTGGACAAATCAAAATTATAATTTGGTTCAGGGCCTCCCTCCAGCGACGGCCGAACTGGGCTAGCCATGCCCATAGTAGGACTAGCAAAACGGAGGGACTAGCCATAGTGGCGAGCTCCCTGGGTGGTCTAAGTCCTGAGTACAGGACAGTCGTCAGTAGTTCGACGTGAGCAGAAGCCCACCTCGAGATGCTACGTGGACGAGGGCATGCCCAAGACACACCTTAACCCTAGCGGGGGTCGCTAGGGTGAAATCACACCACGTGAT
>OQ883956.1
GTATACGAGGTTAGTTCATTCTCGTATGCATGATTGGACAAAACAAAATTTCAATTTGGTTCAGGGCCCCCCTCCAGCGACGGCCGAACTGGGCTAGCCATGCCCATAGTAGGACTAGCAAACGGAGGGACTAGCCGTAGTGGCGAGCTCCCTGGGTGGTCTAAGTCCCGAGTACAGGACAGTCGTCAGTAGTTCGACGTGAGCAGAAGCCCACCTCGAGATGCTATGTGGACGAGGGCATGCCCAAGACACACCTTAACCCTAGCGGGGGTCGCTAGGGTGAAATCACACCACGTGATG
EOF
MSA_IN="/tmp/csfv_msa.fa"
if [[ "$RUN" == container ]]; then
  "${COMPOSE[@]}" cp /tmp/csfv_msa.fa agentd:/tmp/csfv_msa.fa 2>/dev/null || true
  MSA_IN="/tmp/csfv_msa.fa"
fi
ALN="$(exec_blast "$MAFFT_CMD --auto --quiet '$MSA_IN' 2>&1")"
if [[ "$?" -eq 0 && -n "$ALN" ]]; then
  N_IN="$(grep -c '^>' /tmp/csfv_msa.fa)"
  N_OUT="$(echo "$ALN" | grep -c '^>')"
  if [[ "$N_OUT" -eq "$N_IN" ]]; then
    ok "MAFFT 比对成功: $N_IN 条输入 → $N_OUT 条输出"
    WIDTH="$(echo "$ALN" | awk '!/^>/{print length($0)}' | sort -u | head -1)"
    info "比对宽度: $WIDTH 列（应 ≥300）"
  else
    bad "MAFFT 输出条数不符: 输入=$N_IN 输出=$N_OUT"
    echo "$ALN" | head -5
  fi
else
  bad "MAFFT 执行失败: $ALN"
fi

echo "==================== 结果: PASS=$PASS FAIL=$FAIL ===================="
[[ "$FAIL" -eq 0 ]] && echo "✅ CSFV 库 + MAFFT 均可正常比对" || echo "⚠️ 存在失败项，请把完整输出发给 Agent"