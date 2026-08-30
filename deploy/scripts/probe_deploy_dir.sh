#!/usr/bin/env bash
# 探测逻辑: 扫描部署目录，按版本号排序取最新，并校验是否运行态（catalog 资源数 > 4）
pick_deploy_dir() {
  local candidates=() d
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do
    [[ -d "$d" ]] && candidates+=("$d")
  done
  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo ""
    return 1
  fi
  # 按版本号排序（1.0.2 < 1.1.2），取最后一个=最新
  printf '%s\n' "${candidates[@]}" | sort -V | tail -1
}

main() {
  local DEPLOY_DIR="${1:-}"
  if [[ -z "$DEPLOY_DIR" ]]; then
    DEPLOY_DIR="$(pick_deploy_dir)"
    [[ -z "$DEPLOY_DIR" ]] && { echo "[FATAL] 未找到部署目录"; exit 1; }
  fi
  echo "[INFO] 候选部署目录(按版本排序):"
  for d in /opt/oligosmith/agentd-primer-probe-server-*; do
    [[ -d "$d" ]] && echo "  $d"
  done
  echo "[INFO] 选中: $DEPLOY_DIR"

  # 运行态校验: 有 catalog 且资源数 > 4（模板为4, 运行态≥19）
  local catalog="$DEPLOY_DIR/deploy/server-config/database-catalog.json"
  if [[ -f "$catalog" ]]; then
    local n
    n="$(python3 -c "import json;print(len(json.load(open('$catalog',encoding='utf-8')).get('resources',{})))" 2>/dev/null || echo 0)"
    echo "[INFO] catalog 资源数: $n"
    if (( n > 4 )); then
      echo "[INFO] ✅ 运行态 catalog（非模板）"
    else
      echo "[WARN] 资源数≤4，可能是模板目录而非运行目录，请人工确认"
    fi
  fi
}

main "$@"