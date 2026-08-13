ARG VLLM_BASE_IMAGE=vllm/vllm-openai:v0.26.0
FROM ${VLLM_BASE_IMAGE}

ARG VLLM_OMNI_COMMIT=bbe6ccc512a404a2df8c977ea29003002f2683e8

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VLLM_OMNI_TARGET_DEVICE=cuda \
    HF_HOME=/runpod-volume/huggingface-cache \
    MODEL_ID=IndexTeam/IndexTTS-2.5 \
    VLLM_BASE_URL=http://127.0.0.1:8092 \
    VLLM_PORT=8092 \
    VLLM_STARTUP_TIMEOUT=1800 \
    FLASHINFER_DISABLE_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-worker.txt /tmp/requirements-worker.txt

# vLLM-Omni changes rapidly. Pin the exact source revision that was inspected
# with the matching vLLM 0.26 base image instead of following a moving branch.
RUN uv pip install --python "$(command -v python3)" --no-cache-dir \
      "vllm-omni[indextts2] @ git+https://github.com/vllm-project/vllm-omni.git@${VLLM_OMNI_COMMIT}" \
    && uv pip install --python "$(command -v python3)" --no-cache-dir \
      -r /tmp/requirements-worker.txt

WORKDIR /app
COPY deploy /app/deploy
COPY worker /app/worker
COPY handler.py lb_app.py lb_fallback.py start.sh start-lb.sh /app/

RUN chmod 0755 /app/start.sh /app/start-lb.sh \
    && python3 -m compileall -q /app/handler.py /app/lb_app.py /app/lb_fallback.py /app/worker

ENTRYPOINT []
CMD ["/app/start-lb.sh"]
