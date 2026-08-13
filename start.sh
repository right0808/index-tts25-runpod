#!/usr/bin/env bash
set -Eeuo pipefail

MODEL_ID="${MODEL_ID:-IndexTeam/IndexTTS-2.5}"
VLLM_PORT="${VLLM_PORT:-8092}"
DEPLOY_CONFIG="${DEPLOY_CONFIG:-/app/deploy/indextts2_5_serverless.yaml}"
VLLM_LOG_FILE="${VLLM_LOG_FILE:-/tmp/vllm-server.log}"
VLLM_FAILURE_FILE="${VLLM_FAILURE_FILE:-/tmp/vllm-server.failed}"
export VLLM_FAILURE_FILE

server_pid=""
handler_pid=""

cleanup() {
  if [[ -n "${handler_pid}" ]]; then
    kill -TERM "${handler_pid}" 2>/dev/null || true
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
  exit "${exit_code}"
) &
server_pid=$!

# Register with the RunPod queue immediately. Model readiness is awaited by the
# handler after it receives a job so RunPod does not recycle an unregistered
# worker while a large model is still loading.
echo "stage=handler_start"
python3 -u /app/handler.py &
handler_pid=$!

exit_code=0
while kill -0 "${handler_pid}" 2>/dev/null; do
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    wait "${server_pid}" || exit_code=$?
    echo "stage=model_server_failed reason=process_exited exit_code=${exit_code}" >&2
    # Keep the queue handler alive so a diagnostic request can report the
    # bounded startup log captured in VLLM_FAILURE_FILE.
    wait "${handler_pid}" || exit_code=$?
    exit "${exit_code}"
  fi
  sleep 5
done

wait "${handler_pid}" || exit_code=$?
echo "stage=handler_failed reason=process_exited exit_code=${exit_code}" >&2
exit "${exit_code}"
