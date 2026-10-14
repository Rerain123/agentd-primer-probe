#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

MODE="install"
PULL=false
SKIP_VERIFY=false

usage() {
  cat <<'EOF'
用法: install.sh [选项]

选项:
  --build-only   只构建镜像；允许数据库版本和文件尚未就绪
  --no-build     使用已经 docker load 或此前构建的镜像
  --pull         构建时主动刷新基础镜像
  --skip-verify  启动后不调用 HTTP 验收脚本
  -h, --help     显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only) MODE="build-only" ;;
    --no-build) MODE="no-build" ;;
    --pull) PULL=true ;;
    --skip-verify) SKIP_VERIFY=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf '未知参数: %s\n' "$1" >&2; usage >&2; exit 64 ;;
  esac
  shift
done

if [[ ! -f "${AGENTD_ENV_FILE}" ]]; then
  cp "${AGENTD_DEPLOY_DIR}/.env.example" "${AGENTD_ENV_FILE}"
  chmod 600 "${AGENTD_ENV_FILE}"
  printf '[INFO] 已创建 %s；请在生产启动前核对端口和数据目录\n' "${AGENTD_ENV_FILE}"
fi

for variable_and_default in \
  "AGENTD_BLASTDB_DIR=./server-data/blastdb" \
  "AGENTD_SEQUENCE_DATA_DIR=./server-data/sequences"; do
  variable="${variable_and_default%%=*}"
  fallback="${variable_and_default#*=}"
  data_dir="$(agentd_resolve_host_path "$(agentd_read_env "${variable}" "${fallback}")")"
  if [[ "${data_dir}" == "/" ]]; then
    printf '[FAIL] 拒绝把根目录作为数据挂载: %s\n' "${variable}" >&2
    exit 1
  fi
  mkdir -p -- "${data_dir}"
done

require_databases="$(agentd_read_env AGENTD_REQUIRE_DATABASES true)"
if [[ "${MODE}" == "build-only" || "${require_databases,,}" == "false" ]]; then
  bash "${SCRIPT_DIR}/preflight.sh" --allow-placeholders
  if [[ "${MODE}" != "build-only" ]]; then
    printf '[WARN] AGENTD_REQUIRE_DATABASES=false：允许缺少数据库启动；相关科学证据必须保持 NOT_EVALUATED\n'
  fi
else
  bash "${SCRIPT_DIR}/preflight.sh"
fi

if [[ "${MODE}" != "no-build" ]]; then
  build_args=(build)
  if [[ "${PULL}" == true ]]; then
    build_args+=(--pull)
  fi
  printf '[INFO] 构建镜像 %s\n' "$(agentd_image_name)"
  agentd_compose "${build_args[@]}"
elif ! docker image inspect "$(agentd_image_name)" >/dev/null 2>&1; then
  printf '[FAIL] 本机没有镜像 %s；请先构建或 docker load\n' "$(agentd_image_name)" >&2
  exit 1
fi

if [[ "${MODE}" == "build-only" ]]; then
  printf '[PASS] 镜像构建完成。数据库准备好后重新运行 install.sh\n'
  exit 0
fi

printf '[INFO] 在最终容器环境中检查科学工具和数据库\n'
agentd_compose run --rm --no-deps agentd python -m agentd readiness

printf '[INFO] 启动 Agent D 服务\n'
agentd_compose up -d --remove-orphans --no-build

if [[ "${SKIP_VERIFY}" == false ]]; then
  bash "${SCRIPT_DIR}/verify.sh"
fi

printf '[PASS] OligoSmith 1.2.15 MCP 部署完成\n'
printf '[INFO] 查看日志: docker compose --env-file %q -f %q logs -f agentd\n' "${AGENTD_ENV_FILE}" "${AGENTD_COMPOSE_FILE}"
