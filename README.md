# IndexTTS 2.5 on RunPod Serverless

This branch targets a RunPod **Load Balancing** endpoint. It exposes a direct
HTTP API at `https://ENDPOINT_ID.api.runpod.ai/tts` and a liveness check at
`/ping`. The `main` branch retains the Queue worker contract.

The `/tts` body can be either the direct synthesis object shown below or the
same object nested under `input` for compatibility with Queue clients. During
cold start it returns HTTP 503 with `MODEL_STARTING`; retry after the response's
`Retry-After` interval. `/ready` distinguishes `starting`, `ready`, and
`failed` model states.

The container listens on `PORT=8000` for user traffic and on the distinct
`PORT_HEALTH=8001` for RunPod health probes. Configure the template HTTP port
as `8000/http` and `HEALTH_CHECK_PATH=/ping`.

Production-oriented Load Balancing worker for `IndexTeam/IndexTTS-2.5`. It runs the
official vLLM-Omni two-stage backend inside the worker, validates and resolves
speaker/emotion reference audio, synthesizes 22.05 kHz mono WAV, uploads the
result to the configured OSS service, and returns a temporary public URL.

## API

Submit an async RunPod request to `https://api.runpod.ai/v2/ENDPOINT_ID/run`:

```json
{
  "input": {
    "text": "大概两到三天就可以送到哈。",
    "language": "zh",
    "speaker_audio": "https://example.com/speaker.m4a",
    "emotion_audio": "https://example.com/emotion.m4a",
    "emotion_alpha": 0.8,
    "speed": 1.0,
    "text_normalization": true
  }
}
```

`speaker_audio` is required and accepts a public HTTP(S) URL or an audio data
URL. Exactly one optional emotion mode can be used:

- `emotion_audio`: public URL or audio data URL.
- `emotion_vector`: eight values ordered as happy, angry, sad, afraid,
  disgusted, melancholic, surprised, calm.
- `emotion_text`: natural-language emotion description.

Supported public language codes are `zh`, `en`, `zhen`, `ja`, `es`, `ar`, and
`yue`. Speed uses the model-native range `0.5` through `2.0`.

Successful output:

```json
{
  "audio_url": "http://oss.example/oss/generated.wav",
  "file_name": "generated.wav",
  "content_type": "audio/wav",
  "bytes": 123456,
  "sample_rate": 22050,
  "channels": 1,
  "duration_seconds": 5.2,
  "expires_in_seconds": 3600,
  "elapsed_ms": 12345
}
```

Input errors return `output.error` with `retryable: false`. Infrastructure,
inference, and OSS failures fail the RunPod job so operational retries and logs
remain visible.

## RunPod deployment

1. In RunPod Settings, connect GitHub and grant access to this repository.
2. Create a Serverless endpoint and choose **Import Git Repository**.
3. Select this repository, branch `main`, and `/Dockerfile`.
4. Select **Queue** endpoint type.
5. Under Model, enter `IndexTeam/IndexTTS-2.5` to enable RunPod model caching.
6. Start with a single 24 GB GPU priority such as RTX 4090, L4, or A5000.
7. Use `workersMin=0`, `workersMax=1`, execution timeout `1800` seconds,
   container disk at least `64 GB`, CUDA `13.0`, and FlashBoot enabled.
8. Create a RunPod Secret named `OSS_UPLOAD_API_KEY`, then configure:

```text
OSS_BASE_URL=http://your-oss.example.com
OSS_UPLOAD_API_KEY={{ RUNPOD_SECRET_OSS_UPLOAD_API_KEY }}
OSS_EXPIRES_SECONDS=3600
OSS_HTTP_TIMEOUT=30
MODEL_ID=IndexTeam/IndexTTS-2.5
VLLM_STARTUP_TIMEOUT=1800
VLLM_REQUEST_TIMEOUT=1800
```

The Docker image uses `vllm/vllm-openai:v0.26.0` plus the inspected
vLLM-Omni source commit `bbe6ccc512a404a2df8c977ea29003002f2683e8`.
The deploy config follows the official IndexTTS 2.5 two-stage recipe but uses
one sequence at a time and disables S2Mel torch compilation for a safer first
24 GB deployment. A 24 GB profile is not yet officially validated; if the
worker logs a CUDA OOM during initialization, move to a 48 GB or larger GPU
based on the measured peak rather than repeatedly restarting it.

## Tests

```bash
python -m pytest -q
```

After the endpoint is ready, run a real test without committing credentials or
audio files:

```bash
python scripts/live_test.py \
  --endpoint-id ENDPOINT_ID \
  --env-file ../.env \
  --speaker ../fxd.m4a \
  --emotion ../emotion1.m4a \
  --text '大概两到三天就可以送到哈，收到以后您可以品鉴一下哦如果觉得好喝也可以找我，您保留好我的微信有任何问题就随时找我哦'
```

## Security and license

- Credentials are runtime-only. `.env`, private keys, and audio files are
  excluded from Git and Docker build contexts.
- Reference downloads reject non-public IP destinations, limit redirects and
  size, and are converted to local data URLs before reaching vLLM-Omni.
- Logs include stage, task ID, media metadata, output size, and timing, but do
  not include API keys, audio payloads, or full request bodies.
- Use only voices for which you have consent and comply with the official
  [Bilibili IndexTTS model license](https://github.com/index-tts/index-tts/blob/main/LICENSE).
