#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

INPUT=""
OUTPUT=""
TITLE="AgentD nucleotide database"
IMAGE="$(agentd_image_name)"

usage() {
  cat <<'EOF'
用法: build_blast_db.sh --input sequences.fasta --output /path/version/db [选项]

选项:
  --title 文本   BLAST 数据库标题
  --image 镜像   未安装 makeblastdb 时使用的容器镜像

脚本拒绝覆盖已有索引。升级数据库时请使用新的版本目录，再修改 database-catalog.json。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) INPUT="${2:-}"; shift ;;
    --output) OUTPUT="${2:-}"; shift ;;
    --title) TITLE="${2:-}"; shift ;;
    --image) IMAGE="${2:-}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf '未知参数: %s\n' "$1" >&2; usage >&2; exit 64 ;;
  esac
  shift
done

[[ -n "${INPUT}" && -n "${OUTPUT}" ]] || { usage >&2; exit 64; }
[[ -f "${INPUT}" ]] || { printf '[FAIL] FASTA 不存在: %s\n' "${INPUT}" >&2; exit 1; }

input_dir="$(cd -- "$(dirname -- "${INPUT}")" && pwd)"
input_name="$(basename -- "${INPUT}")"
output_parent="$(dirname -- "${OUTPUT}")"
output_name="$(basename -- "${OUTPUT}")"
mkdir -p -- "${output_parent}"
output_parent="$(cd -- "${output_parent}" && pwd)"
output_prefix="${output_parent}/${output_name}"

shopt -s nullglob
existing=("${output_prefix}".n* "${output_prefix}".nal)
shopt -u nullglob
if (( ${#existing[@]} > 0 )); then
  printf '[FAIL] 输出前缀已有文件，拒绝覆盖: %s\n' "${output_prefix}" >&2
  exit 1
fi

if command -v makeblastdb >/dev/null 2>&1; then
  makeblastdb -in "${input_dir}/${input_name}" -dbtype nucl -parse_seqids -title "${TITLE}" -out "${output_prefix}"
else
  command -v docker >/dev/null 2>&1 || { printf '[FAIL] makeblastdb 和 docker 均不可用\n' >&2; exit 1; }
  docker image inspect "${IMAGE}" >/dev/null 2>&1 || { printf '[FAIL] 镜像不存在: %s；请先运行 install.sh --build-only\n' "${IMAGE}" >&2; exit 1; }
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "${input_dir}:/input:ro" \
    -v "${output_parent}:/output" \
    "${IMAGE}" makeblastdb \
    -in "/input/${input_name}" -dbtype nucl -parse_seqids -title "${TITLE}" -out "/output/${output_name}"
fi

for suffix in .nhr .nin .nsq; do
  if ! compgen -G "${output_prefix}*${suffix}" >/dev/null; then
    printf '[FAIL] 建库后缺少 %s 索引\n' "${suffix}" >&2
    exit 1
  fi
done
printf '[PASS] BLAST 数据库建立完成: %s\n' "${output_prefix}"
