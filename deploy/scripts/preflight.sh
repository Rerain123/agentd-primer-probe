#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ALLOW_PLACEHOLDERS=false
if [[ "${1:-}" == "--allow-placeholders" ]]; then
  ALLOW_PLACEHOLDERS=true
elif [[ -n "${1:-}" ]]; then
  printf '用法: %s [--allow-placeholders]\n' "$0" >&2
  exit 64
fi

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

warn() {
  printf '[WARN] %s\n' "$1" >&2
}

pass() {
  printf '[PASS] %s\n' "$1"
}

for command_name in docker curl; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "缺少命令: ${command_name}"
done
pass "基础命令存在"

docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2（docker compose）"
docker info >/dev/null 2>&1 || fail "Docker daemon 不可访问；请启动 Docker 并检查当前用户权限"
pass "Docker 与 Compose 可用"

[[ -f "${AGENTD_ENV_FILE}" ]] || fail "缺少 ${AGENTD_ENV_FILE}；先复制 deploy/.env.example 为 deploy/.env"
for required_file in \
  "${AGENTD_COMPOSE_FILE}" \
  "${AGENTD_DEPLOY_DIR}/server-config/production.json" \
  "${AGENTD_DEPLOY_DIR}/server-config/database-catalog.json"; do
  [[ -f "${required_file}" ]] || fail "缺少部署文件: ${required_file}"
done
pass "部署文件完整"

agentd_compose config --quiet || fail "Docker Compose 配置解析失败"
pass "Docker Compose 配置有效"

port="$(agentd_read_env AGENTD_PORT 8000)"
[[ "${port}" =~ ^[0-9]+$ ]] || fail "AGENTD_PORT 必须是整数"
(( port >= 1 && port <= 65535 )) || fail "AGENTD_PORT 必须在 1-65535 范围内"

blast_dir="$(agentd_resolve_host_path "$(agentd_read_env AGENTD_BLASTDB_DIR ./server-data/blastdb)")"
sequence_dir="$(agentd_resolve_host_path "$(agentd_read_env AGENTD_SEQUENCE_DATA_DIR ./server-data/sequences)")"

for data_dir in "${blast_dir}" "${sequence_dir}"; do
  if [[ ! -d "${data_dir}" ]]; then
    if [[ "${ALLOW_PLACEHOLDERS}" == true ]]; then
      warn "数据目录尚不存在: ${data_dir}"
    else
      fail "数据目录不存在: ${data_dir}"
    fi
  elif [[ ! -r "${data_dir}" || ! -x "${data_dir}" ]]; then
    fail "当前用户无法读取数据目录: ${data_dir}"
  fi
done
pass "数据挂载目录检查完成"

catalog="${AGENTD_DEPLOY_DIR}/server-config/database-catalog.json"
if grep -q 'SET_ON_SERVER' "${catalog}"; then
  warn "数据库目录仍包含 SET_ON_SERVER；必需资源若未填写真实版本，将无法通过启动前 readiness"
fi

if [[ "${ALLOW_PLACEHOLDERS}" == false ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python3 "${SCRIPT_DIR}/validate_catalog.py" --env-file "${AGENTD_ENV_FILE}" --catalog "${catalog}"
  else
    warn "主机未安装 python3，跳过宿主侧目录深度检查；容器构建后仍会执行 /ready 检查"
  fi
fi

pass "服务器部署前检查完成"
