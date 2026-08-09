#!/usr/bin/env bash
# Espera o Railway detectar e concluir o redeploy de um serviço depois de um
# git push, comparando o deployment ID antes/depois. Não existe webhook de
# "build terminou" no CLI da Railway — só dá pra saber por polling.
#
# Uso:
#   scripts/aguardar_deploy.sh [--service NOME] [--max-checks N] [--interval SEGUNDOS]
#
# Sem --service, usa o serviço linkado por padrão (railway status).
# Sai com 0 quando detecta deployment ID novo E status "Online".
# Sai com 1 se esgotar as tentativas sem ver os dois sinais.

set -euo pipefail

SERVICE=""
MAX_CHECKS=20
INTERVAL=15

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="$2"; shift 2 ;;
    --max-checks) MAX_CHECKS="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
  esac
done

_status_block() {
  if [[ -n "$SERVICE" ]]; then
    railway status 2>&1 | grep -A2 "^$SERVICE"
  else
    railway status 2>&1
  fi
}

_deployment_id() {
  _status_block | grep -i "deployment ID" | head -1 | awk '{print $3}'
}

_service_status_linha() {
  _status_block | grep "status:" | head -1
}

ID_ANTIGO=$(_deployment_id)
echo "deployment ID antes do push: ${ID_ANTIGO:-<nenhum>}"

for i in $(seq 1 "$MAX_CHECKS"); do
  ID_ATUAL=$(_deployment_id)
  if [[ -n "$ID_ATUAL" && "$ID_ATUAL" != "$ID_ANTIGO" ]]; then
    echo "check $i: novo deployment ID detectado — $ID_ATUAL"
    break
  fi
  echo "check $i: ainda no deployment antigo ($ID_ANTIGO)..."
  sleep "$INTERVAL"
done

if [[ "$ID_ATUAL" == "$ID_ANTIGO" ]]; then
  echo "esgotou $MAX_CHECKS tentativas sem detectar novo deployment" >&2
  exit 1
fi

for i in $(seq 1 "$MAX_CHECKS"); do
  LINHA=$(_service_status_linha)
  echo "$LINHA"
  if ! echo "$LINHA" | grep -qi "Deploying"; then
    if echo "$LINHA" | grep -qi "Online"; then
      echo "deploy concluído, serviço Online."
      exit 0
    fi
    echo "status inesperado (nem Deploying nem Online) — confira manualmente." >&2
    exit 1
  fi
  sleep "$INTERVAL"
done

echo "esgotou $MAX_CHECKS tentativas esperando sair de 'Deploying'" >&2
exit 1
