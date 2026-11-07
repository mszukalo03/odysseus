---
layout: default
---

# Speech on this device

Odysseus can run speech-to-text (STT) and text-to-speech (TTS) directly on each
client device instead of on the Odysseus server. Each browser stores its own
**Speech on this device** configuration in `localStorage` (`deviceSpeechSettings`),
so a desktop with a GPU, a laptop on CPU, and a phone browser can each use the
right backend without changing server settings.

## Resolution order

For every STT and TTS call (`static/js/deviceSpeech.js`, `voiceRecorder.js`,
`voiceMode.js`, `tts-ai.js`), the browser tries:

1. **Device server** (if **Speech on this device** is enabled and `GET {url}/models`
   responds within ~1 s; the probe result is cached for 60 s):
   - STT: `POST {url}/audio/transcriptions` (`multipart/form-data` with `file` and `model`)
   - TTS: `POST {url}/audio/speech` (`application/json` with `model`, `voice`, `input`, `response_format`)
2. **Odysseus server routes** (`/api/stt/transcribe`, `/api/tts/synthesize`) if the
   server reports that speech is configured (`local` or `endpoint:<id>`).
3. **Browser built-in fallback**:
   - STT: Web Speech API (`SpeechRecognition` / `webkitSpeechRecognition`),
     accepted in both the composer microphone recorder and live voice mode.
   - TTS: `window.speechSynthesis`.

If the device server is stopped or unreachable, calls fall through to step 2 or 3
automatically.

## Configuring in Odysseus

Open **Settings → AI Defaults → Speech on this device** (or click the gear icon
in the live voice mode bar):

| Field | Default | Description |
|---|---|---|
| Enable | off | Use the local Speaches server on this device |
| URL | `http://localhost:8000/v1` | OpenAI-compatible base URL on this machine |
| STT model | `Systran/faster-distil-whisper-small.en` | Whisper model ID loaded in Speaches |
| TTS model | `speaches-ai/Kokoro-82M-v1.0-ONNX` | Kokoro ONNX model ID loaded in Speaches |
| TTS voice | `af_heart` | Voice ID passed to `/v1/audio/speech` |
| **Test** | — | Probes `{url}/models` and reports loaded models or a connection/CORS hint |

## Running Speaches on the PC (NVIDIA CUDA)

Run [Speaches](https://speaches.ai) bound to loopback (`127.0.0.1:8000`) with
GPU acceleration:

```bash
docker run -d \
  --name speaches \
  --restart unless-stopped \
  --gpus all \
  -p 127.0.0.1:8000:8000 \
  -e ALLOW_ORIGINS='["https://odysseus.your-tailnet.ts.net", "http://localhost:7011"]' \
  -v speaches-hf-cache:/home/ubuntu/.cache/huggingface \
  ghcr.io/speaches-ai/speaches:latest-cuda
```

Download the models once (STT `Systran/faster-whisper-large-v3-turbo` and TTS
`speaches-ai/Kokoro-82M-v1.0-ONNX`):

```bash
curl -X POST http://127.0.0.1:8000/v1/models/Systran%2Ffaster-whisper-large-v3-turbo
curl -X POST http://127.0.0.1:8000/v1/models/speaches-ai%2FKokoro-82M-v1.0-ONNX
```

Then in the PC browser's **Speech on this device** settings, set **STT model** to
`Systran/faster-whisper-large-v3-turbo`, **TTS model** to
`speaches-ai/Kokoro-82M-v1.0-ONNX`, **TTS voice** to `af_heart`, toggle it on,
and click **Test**.

## Running Speaches on the laptop (CPU)

On a laptop without a dedicated NVIDIA GPU, use the CPU image with
`Systran/faster-distil-whisper-small.en` (int8 by default on CPU in
faster-whisper) and `speaches-ai/Kokoro-82M-v1.0-ONNX`:

```bash
docker run -d \
  --name speaches \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -e ALLOW_ORIGINS='["https://odysseus.your-tailnet.ts.net", "http://localhost:7011"]' \
  -e WHISPER__COMPUTE_TYPE=int8 \
  -v speaches-hf-cache:/home/ubuntu/.cache/huggingface \
  ghcr.io/speaches-ai/speaches:latest-cpu
```

Download the models once:

```bash
curl -X POST http://127.0.0.1:8000/v1/models/Systran%2Ffaster-distil-whisper-small.en
curl -X POST http://127.0.0.1:8000/v1/models/speaches-ai%2FKokoro-82M-v1.0-ONNX
```

The default model fields in Odysseus already match this laptop setup
(`Systran/faster-distil-whisper-small.en`, `speaches-ai/Kokoro-82M-v1.0-ONNX`,
`af_heart`).

## CORS and HTTPS / Local Network Access

When Odysseus is served over HTTPS (e.g. Tailscale Serve at
`https://<host>.<tailnet>.ts.net`) and calls `http://localhost:8000/v1`:

1. **Mixed content**: Modern browsers treat `http://localhost` and
   `http://127.0.0.1` as potentially trustworthy origins, so `fetch()` from an
   HTTPS page is allowed. Odysseus' `Content-Security-Policy` allows
   `http://localhost:*` and `http://127.0.0.1:*` in `connect-src` and `media-src`.
2. **Chrome Local Network Access (LNA) / Private Network Access**: Chrome may
   show a permission prompt asking to allow the HTTPS site to connect to devices
   on your local network / loopback. Click **Allow**. If blocked or dismissed,
   Odysseus catches the rejected `fetch`, shows a hint in the **Test** output,
   and falls through to the next speech option.
3. **Speaches CORS**: Because the browser sends a cross-origin request from your
   Odysseus origin to `http://localhost:8000`, Speaches must allow that origin.
   Speaches uses Pydantic settings and reads `ALLOW_ORIGINS` as a JSON list of
   allowed origin strings (for example
   `-e ALLOW_ORIGINS='["https://odysseus.your-tailnet.ts.net"]'` or
   `-e ALLOW_ORIGINS='["*"]'`).
   *(Note: `ALLOW_ORIGINS` and `WHISPER__COMPUTE_TYPE` env var names are based on
   Speaches' documented Pydantic `Config` fields and were not live-tested against
   a running container during this build — verify with `docker logs speaches` on
   first start.)*

## Phone (Fold 7, Chrome on Android)

Leave **Speech on this device** off on the phone. When the Odysseus server has
no server-side speech configured, both the composer mic button and live voice
mode automatically fall back to Chrome Android's built-in `webkitSpeechRecognition`
and `speechSynthesis`.

### Experimental: local Speaches in Termux / proot on Fold 7

> **Experimental.** Running Speaches locally on Android via Termux (`proot-distro`
> Ubuntu ARM64) is possible if you want offline/Kokoro voice on the phone without
> browser speech:
>
> 1. Install Termux and `proot-distro install ubuntu && proot-distro login ubuntu`.
> 2. Inside Ubuntu ARM64, install Python 3.11+, `ffmpeg`, and `uv`, then install
>    `speaches` (or run `faster-whisper` + `kokoro-onnx` via `uvicorn` on
>    `127.0.0.1:8000` with `ALLOW_ORIGINS='["*"]'`).
> 3. Keep Termux wakelock enabled so Android does not suspend the process mid-chat,
>    and enable **Speech on this device** (`http://localhost:8000/v1`) in mobile
>    Chrome. Battery and thermal overhead are higher than the built-in browser
>    speech fallback.
