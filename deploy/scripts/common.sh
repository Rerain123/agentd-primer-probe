#!/usr/bin/env bash

AGENTD_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGENTD_DEPLOY_DIR="$(cd -- "${AGENTD_SCRIPT_DIR}/.." && pwd)"
AGENTD_PROJECT_ROOT="$(cd -- "${AGENTD_DEPLOY_DIR}/.." && pwd)"
AGENTD_ENV_FILE="${AGENTD_ENV_FILE:-${AGENTD_DEPLOY_DIR}/.env}"
AGENTD_COMPOSE_FILE="${AGENTD_DEPLOY_DIR}/docker-compose.yml"

agentd_read_env() {
  local key="$1"
  local fallback="${2:-}"
  local value=""
  if [[ -f "${AGENTD_ENV_FILE}" ]]; then
    value="$(awk -F= -v wanted="${key}" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "${AGENTD_ENV_FILE}")"
  fi
  value="${value%$'\r'}"
  value="${value#\"}"
  value="${value%\"}"
  printf '%s\n' "${value:-${fallback}}"
}

agentd_resolve_host_path() {
  local configured="$1"
  if [[ "${configured}" == /* ]]; then
    printf '%s\n' "${configured}"
  else
    printf '%s\n' "${AGENTD_DEPLOY_DIR}/${configured#./}"
  fi
}

agentd_compose() {
  docker compose --env-file "${AGENTD_ENV_FILE}" -f "${AGENTD_COMPOSE_FILE}" "$@"
}

agentd_image_name() {
  local image_tag
  image_tag="$(agentd_read_env AGENTD_IMAGE_TAG 1.0.3)"
  printf 'agentd-primer-probe:%s\n' "${image_tag}"
}
