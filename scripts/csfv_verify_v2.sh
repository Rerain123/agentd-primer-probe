#!/usr/bin/env bash
# =============================================================================
# CSFV 靶标专库 + MAFFT 多序列比对 服务器验证脚本 v2
# v2 修复: ① .nsq 精确定位 db 前缀（不再假设目录层级）
#          ② 容器路径 /srv/oligosmith-data/blastdb -> /data/blastdb（替换而非删除）
#          ③ 文件传入容器改用 stdin 重定向（不依赖 docker compose cp）
# 用法:
#   bash csfv_verify_v2.sh [部署目录]      # 可选参数指定部署目录
# =============================================================================
set -uo pipefail

PASS=0; FAIL=0
ok()   { echo -e "  \033[32m[PASS]\033[0m $*"; PASS=$((PASS+1)); }
bad()  { echo -e "  \033[31m[FAIL]\033[0m $*"; FAIL=$((FAIL+1)); }
info() { echo -e "  \033[36m[INFO]\033[0m $*"; }

echo "==================== CSFV 库验证 v2 ===================="

# ---------- 0. 定位部署目录 ----------
DEPLOY_DIR="${1:-}"
if [[ -z "$DEPLOY_DIR" ]]; then
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do
    [[ -d "$d" ]] && DEPLOY_DIR="$d" && break
  done
fi
[[ -z "$DEPLOY_DIR" || ! -d "$DEPLOY_DIR" ]] && { echo "[FATAL] 找不到部署目录，用参数指定: bash $0 /path/to/deploy"; exit 1; }
info "部署目录: $DEPLOY_DIR"
COMPOSE=(docker compose --env-file "$DEPLOY_DIR/deploy/.env" -f "$DEPLOY_DIR/deploy/docker-compose.yml")

# ---------- 1. 精准定位 CSFV BLAST 库（找 .nsq 文件） ----------
info "定位 CSFV blastdb .nsq ..."
NSQ="$(find /srv -maxdepth 8 \( -iname "*.nsq" \) -path "*csfv*" 2>/dev/null | head -1)"
if [[ -z "$NSQ" ]]; then
  info "宿主 /srv 未找到，列出 target 目录树辅助诊断:"
  find /srv/oligosmith-data/blastdb/target /srv/agentd-data/blastdb/target -maxdepth 3 -type d 2>/dev/null | head -30
  bad "未找到 CSFV .nsq"
  exit 1
fi
DB_PREFIX="${NSQ%.nsq}"
info "CSFV db 前缀(宿主): $DB_PREFIX"

# 容器内对应前缀：/srv/oligosmith-data/blastdb 或 /srv/agentd-data/blastdb -> /data/blastdb
CONTAINER_PREFIX="${DB_PREFIX//\/srv\/oligosmith-data\/blastdb/\/data\/blastdb}"
CONTAINER_PREFIX="${CONTAINER_PREFIX//\/srv\/agentd-data\/blastdb/\/data\/blastdb}"
info "CSFV db 前缀(容器): $CONTAINER_PREFIX"

# ---------- 2. 索引三件套 ----------
info "检查索引文件 ..."
if ls "${DB_PREFIX}".nhr "${DB_PREFIX}".nin "${DB_PREFIX}".nsq >/dev/null 2>&1; then
  ok "索引三件套存在: ${DB_PREFIX}.{nhr,nin,nsq}"
else
  ls -la "$(dirname "$DB_PREFIX")" 2>/dev/null | sed 's/^/      /'
  bad "索引文件缺失: ${DB_PREFIX}.{nhr,nin,nsq}"
fi

# ---------- 3. 执行环境：宿主优先，否则容器 ----------
if command -v blastdbcmd >/dev/null 2>&1; then
  RUN=host;   RUN_PREFIX="$DB_PREFIX"
  info "使用宿主 blast+"
else
  RUN=container; RUN_PREFIX="$CONTAINER_PREFIX"
  info "宿主无 blast+，容器内执行（prefix=$RUN_PREFIX）"
fi

exec_blast() { # $1=命令体
  if [[ "$RUN" == host ]]; then bash -c "$1"; else "${COMPOSE[@]}" exec -T agentd bash -c "$1"; fi
}

# 传文件进容器（stdin 重定向，不依赖 compose cp）
put_file() { # $1=宿主文件 $2=容器内路径
  if [[ "$RUN" == host ]]; then cp "$1" "$2"; else "${COMPOSE[@]}" exec -T agentd bash -c "cat > '$2'" < "$1"; fi
}

# ---------- 4. blastdbcmd -info ----------
echo "--- 4. blastdbcmd -info ---"
OUT="$(exec_blast "$(command -v blastdbcmd 2>/dev/null || echo blastdbcmd) -db '$RUN_PREFIX' -info 2>&1")"
if [[ "$?" -eq 0 && "$OUT" == *"sequences"* ]]; then
  ok "blastdbcmd 可读取索引"
  echo "$OUT" | sed 's/^/      /'
else
  bad "blastdbcmd 读取失败: $OUT"
fi

# ---------- 5. blastn 真实比对 ----------
echo "--- 5. blastn 真实比对 (5'UTR 片段查库) ---"
cat > /tmp/csfv_q.fa <<'EOF'
>q_csfv_5utr
GTATACGAGGTTAGTTCATTCTCGTATGCATGATTGGACAAATCAAAATTTCAATTTGGTTCAGGGCCTCCCTCCAGCGACGGCCGAACTGGGCTAGCCATGCCCACAGTAGGACTAGCAAACGGAGGGACTAGCCGTAGTGGCGAGCTCCCTGGGTGGTCTAAGTCCTGAGTACAGGACAGTCGTCAGTAGTTCGACGTGAGCAGAAGCCCACCTCGAGATGCTATGTGGACGAGGGCATGCCCAAGACACACCTTAACCCTAGCGGGGGTCGCTAGGGTGAAATCACACCACGTGATG
EOF
put_file /tmp/csfv_q.fa /tmp/csfv_q.fa
HITS="$(exec_blast "blastn -db '$RUN_PREFIX' -query '/tmp/csfv_q.fa' -outfmt '6 qseqid sseqid pident length evalue bitscore' 2>&1")"
if [[ "$?" -eq 0 && -n "$HITS" && "$HITS" != *"error"* ]]; then
  ok "blastn 比对成功，命中 $(echo "$HITS" | wc -l) 条:"
  echo "$HITS" | sed 's/^/      /'
else
  bad "blastn 无命中或失败: $HITS"
fi

# ---------- 6. MAFFT 多序列比对 ----------
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
put_file /tmp/csfv_msa.fa /tmp/csfv_msa.fa
ALN="$(exec_blast "mafft --auto --quiet '/tmp/csfv_msa.fa' 2>&1")"
if [[ "$?" -eq 0 && -n "$ALN" ]]; then
  N_IN="$(grep -c '^>' /tmp/csfv_msa.fa)"
  N_OUT="$(echo "$ALN" | grep -c '^>')"
  if [[ "$N_OUT" -eq "$N_IN" ]]; then
    ok "MAFFT 比对成功: $N_IN 条输入 → $N_OUT 条输出"
    WIDTH="$(echo "$ALN" | awk '/^>/{if(seen)exit; seen=1; next} seen{s+=length($0)} END{print s+0}')"
    info "比对宽度: $WIDTH 列（应 ≥300）"
  else
    bad "MAFFT 输出条数不符: 输入=$N_IN 输出=$N_OUT"
  fi
else
  bad "MAFFT 执行失败: $ALN"
fi

echo "==================== 结果: PASS=$PASS FAIL=$FAIL ===================="
[[ "$FAIL" -eq 0 ]] && echo "✅ CSFV 库 + MAFFT 均可正常比对" || echo "⚠️ 存在失败项，请把完整输出发给 Agent"