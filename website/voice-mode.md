---
layout: default
---

# Live voice mode

A hands-free spoken conversation with the current chat: you talk, Odysseus
transcribes when you pause, sends it as a normal message, and speaks the reply
while it streams. When it has finished speaking it listens again. Talk over a
reply to interrupt it.

Start it with the headset button in the composer, `/voice`, or
**Alt+Shift+V**. **Esc** or **End** stops it. The chat, persona, model,
agent/chat mode and tools are whatever the chat already uses, so a vault
persona in Agent mode works by voice too.

## Requirements

- **Server speech-to-text**: Settings STT provider `local` (faster-whisper)
  or an OpenAI-compatible endpoint (e.g. a Speaches or LiteLLM gateway, see
  `ODYSSEUS_SPEECH_BASE_URL` in `.env.example`). The browser Web Speech
  provider isn't supported in voice mode.
- **Text-to-speech**: `local` (Kokoro), an endpoint, or the browser voice.
- **A secure context** for the microphone: HTTPS (e.g. Tailscale Serve) or
  `localhost`.
- Headphones help. Echo cancellation is on, but barge-in on loud speakers can
  trigger on the reply itself; turn barge-in off in the voice settings if so.

## What changes for a voice turn

The message is sent with `voice=true`. The server then:

- adds a short **voice-style system prompt**: speak, don't format; one to
  three sentences; numbers said naturally; ask one clarifying question
  instead of listing options; report tool results, not steps. Admins can
  replace it with the `voice_mode_prompt` setting (empty = built-in default,
  `src/voice_prompt.py`).
- turns **thinking off** unless the composer's Thinking control is set
  explicitly, so the first words arrive sooner.

On the client, TTS speaks short sentences too (normal read-aloud skips
sentences under 15 characters), speaks a long opening clause at its comma,
and synthesizes the next sentence while the current one plays.

## Voice settings (per browser)

The gear in the voice bar:

| Setting | Default | Meaning |
|---|---|---|
| Sensitivity | 3 | Speech is energy above the room's noise floor times this; lower picks up quieter speech |
| Pause before sending | 900 ms | Silence that ends your turn |
| Talk over the reply to interrupt it | on | Barge-in |
| Say "One moment" while tools run | on | Spoken filler when an agent starts a tool |

## Speech next to a local model

If a model runs on another box (e.g. a GPU PC) you can run speech there too
and have Odysseus use it only for chats with that model, keeping the always-on
gateway for everything else:

```bash
ODYSSEUS_SPEECH_LOCAL_BASE_URL=http://pc.your-tailnet.ts.net:8000/v1
ODYSSEUS_STT_LOCAL_MODEL=Systran/faster-whisper-large-v3-turbo
ODYSSEUS_TTS_LOCAL_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX
ODYSSEUS_TTS_LOCAL_VOICE=af_heart
```

This seeds the `stt_provider_local` / `tts_provider_local` settings (plus
model and voice). For each STT/TTS request the browser sends the chat's model
endpoint; when that endpoint is on the same host as the local speech server,
the local server is tried first with a 1.5 s connect timeout, then the default
gateway. Cloud-model chats always use the gateway. See
`src/speech_routing.py`.

## Picking models

- **LLM**: a fast model with thinking off. Locally, Qwen3.5-9B on a 12 GB GPU
  answers in well under a second per sentence; in the cloud, any
  "mini/flash/haiku" tier.
- **STT**: faster-whisper `large-v3-turbo` (GPU, about 1.5 GB) or
  `distil-large-v3` on CPU.
- **TTS**: Kokoro-82M: fast on GPU, faster than real time on a modern CPU.
