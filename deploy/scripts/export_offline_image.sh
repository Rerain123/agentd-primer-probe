#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

IMAGE="$(agentd_image_name)"
OUTPUT="${AGENTD_PROJECT_ROOT}/dist/agentd-primer-probe-${IMAGE##*:}-linux-image.tar.gz"

if [[ "${1:-}" == "--output" ]]; then
  OUTPUT="${2:-}"
elif [[ -n "${1:-}" ]]; then
  printf '用法: %s [--output image.tar.gz]\n' "$0" >&2
  exit 64
fi

command -v docker >/dev/null 2>&1 || { printf '[FAIL] 缺少 docker\n' >&2; exit 1; }
command -v gzip >/dev/null 2>&1 || { printf '[FAIL] 缺少 gzip\n' >&2; exit 1; }
docker image inspect "${IMAGE}" >/dev/null 2>&1 || { printf '[FAIL] 镜像不存在: %s\n' "${IMAGE}" >&2; exit 1; }
[[ ! -e "${OUTPUT}" ]] || { printf '[FAIL] 输出已存在，拒绝覆盖: %s\n' "${OUTPUT}" >&2; exit 1; }

mkdir -p -- "$(dirname -- "${OUTPUT}")"
temporary="${OUTPUT}.partial"
trap 'rm -f -- "${temporary}"' EXIT
docker save "${IMAGE}" | gzip -9 > "${temporary}"
mv -- "${temporary}" "${OUTPUT}"
trap - EXIT

if command -v sha256sum >/dev/null 2>&1; then
  checksum_file="${OUTPUT}.sha256"
  (cd -- "$(dirname -- "${OUTPUT}")" && sha256sum "$(basename -- "${OUTPUT}")") > "${checksum_file}"
  printf '[PASS] 镜像与校验文件: %s, %s\n' "${OUTPUT}" "${checksum_file}"
else
  printf '[PASS] 镜像已导出: %s（主机无 sha256sum，未生成校验文件）\n' "${OUTPUT}"
fi
