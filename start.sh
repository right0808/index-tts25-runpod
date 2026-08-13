#!/usr/bin/env bash
set -Eeuo pipefail

MODEL_ID="${MODEL_ID:-IndexTeam/IndexTTS-2.5}"
VLLM_PORT="${VLLM_PORT:-8092}"
DEPLOY_CONFIG="${DEPLOY_CONFIG:-/app/deploy/indextts2_5_serverless.yaml}"

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
FLASHINFER_DISABLE_VERSION_CHECK=1 vllm serve "${MODEL_ID}" \
  --host 127.0.0.1 \
  --port "${VLLM_PORT}" \
  --omni \
  --trust-remote-code \
  --served-model-name "${MODEL_ID}" \
  --deploy-config "${DEPLOY_CONFIG}" &
server_pid=$!

# Register with the RunPod queue immediately. Model readiness is awaited by the
# handler after it receives a job so RunPod does not recycle an unregistered
# worker while a large model is still loading.
echo "stage=handler_start"
python3 -u /app/handler.py &
handler_pid=$!

while kill -0 "${server_pid}" 2>/dev/null && kill -0 "${handler_pid}" 2>/dev/null; do
  sleep 5
done

exit_code=0
if ! kill -0 "${server_pid}" 2>/dev/null; then
  wait "${server_pid}" || exit_code=$?
  echo "stage=model_server_failed reason=process_exited exit_code=${exit_code}" >&2
else
  wait "${handler_pid}" || exit_code=$?
  echo "stage=handler_failed reason=process_exited exit_code=${exit_code}" >&2
fi
exit "${exit_code}"
