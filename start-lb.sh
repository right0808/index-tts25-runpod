#!/usr/bin/env bash
set -Eeuo pipefail

MODEL_ID="${MODEL_ID:-IndexTeam/IndexTTS-2.5}"
VLLM_PORT="${VLLM_PORT:-8092}"
PORT="${PORT:-8000}"
PORT_HEALTH="${PORT_HEALTH:-8001}"
LB_INTERNAL_PORT="${LB_INTERNAL_PORT:-8002}"
DEPLOY_CONFIG="${DEPLOY_CONFIG:-/app/deploy/indextts2_5_serverless.yaml}"
VLLM_LOG_FILE="${VLLM_LOG_FILE:-/tmp/vllm-server.log}"
VLLM_FAILURE_FILE="${VLLM_FAILURE_FILE:-/tmp/vllm-server.failed}"
export VLLM_FAILURE_FILE

server_pid=""
api_pid=""
app_pid=""
health_pid=""

cleanup() {
  if [[ -n "${api_pid}" ]]; then
    kill -TERM "${api_pid}" 2>/dev/null || true
  fi
  if [[ -n "${app_pid}" ]]; then
    kill -TERM "${app_pid}" 2>/dev/null || true
  fi
  if [[ -n "${health_pid}" ]]; then
    kill -TERM "${health_pid}" 2>/dev/null || true
  fi
  if [[ -n "${server_pid}" ]]; then
    kill -TERM "${server_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "stage=model_server_start model=${MODEL_ID} port=${VLLM_PORT}"
: > "${VLLM_LOG_FILE}"
rm -f "${VLLM_FAILURE_FILE}"
(
  set +e
  FLASHINFER_DISABLE_VERSION_CHECK=1 vllm serve "${MODEL_ID}" \
    --host 127.0.0.1 \
    --port "${VLLM_PORT}" \
    --omni \
    --trust-remote-code \
    --served-model-name "${MODEL_ID}" \
    --deploy-config "${DEPLOY_CONFIG}" 2>&1 | tee -a "${VLLM_LOG_FILE}"
  exit_code=${PIPESTATUS[0]}
  {
    printf 'exit_code=%d\n' "${exit_code}"
    tail -n 80 "${VLLM_LOG_FILE}"
  } > "${VLLM_FAILURE_FILE}"
  echo "stage=model_server_failed reason=process_exited exit_code=${exit_code}" >&2
  exit "${exit_code}"
) &
server_pid=$!

# The liveness endpoint intentionally starts before model readiness. This keeps
# RunPod from recycling the worker during a long IndexTTS cold start. /tts and
# /ready still reject traffic until the local vLLM server reports ready.
echo "stage=load_balancer_gateway_start port=${PORT} upstream_port=${LB_INTERNAL_PORT}"
python3 /app/lb_fallback.py --port "${PORT}" --upstream-port "${LB_INTERNAL_PORT}" &
api_pid=$!

echo "stage=load_balancer_app_start port=${LB_INTERNAL_PORT}"
rm -f /tmp/lb-api.failed
(
  set +e
  python3 -m uvicorn lb_app:app --host 127.0.0.1 --port "${LB_INTERNAL_PORT}" 2>&1 | tee /tmp/lb-api.log
  exit_code=${PIPESTATUS[0]}
  {
    printf 'exit_code=%d\n' "${exit_code}"
    tail -n 80 /tmp/lb-api.log
  } > /tmp/lb-api.failed
  echo "stage=load_balancer_failed reason=process_exited exit_code=${exit_code}" >&2
) &
app_pid=$!

if [[ "${PORT_HEALTH}" != "${PORT}" ]]; then
  echo "stage=health_server_start port=${PORT_HEALTH}"
  python3 /app/lb_fallback.py --port "${PORT_HEALTH}" --health-only &
  health_pid=$!
fi

exit_code=0
if [[ -n "${health_pid}" ]]; then
  wait "${health_pid}" || exit_code=$?
  echo "stage=health_server_failed reason=process_exited exit_code=${exit_code}" >&2
else
  wait "${api_pid}" || exit_code=$?
fi
exit "${exit_code}"
