// static/js/deviceSpeech.js
// Client-side speech (STT/TTS) resolution:
//   (a) Device server (Speaches / OpenAI-compatible on localhost) if enabled
//       and /v1/models responds within ~1 s (cached for 60 s).
//   (b) Odysseus server routes (/api/stt/transcribe, /api/tts/synthesize) if
//       the server reports speech is configured.
//   (c) Browser fallback (SpeechRecognition / speechSynthesis).

import Storage from './storage.js';

export const DEVICE_SPEECH_STORAGE_KEY = 'deviceSpeechSettings';
export const PROBE_TIMEOUT_MS = 1000;
export const PROBE_CACHE_TTL_MS = 60000;

export const DEVICE_SPEECH_DEFAULTS = Object.freeze({
  enabled: false,
  url: 'http://localhost:8000/v1',
  sttModel: 'Systran/faster-distil-whisper-small.en',
  ttsModel: 'speaches-ai/Kokoro-82M-v1.0-ONNX',
  ttsVoice: 'af_heart',
});

let _probeCache = {
  url: '',
  ok: false,
  ts: 0,
  models: [],
  error: '',
};

let _inflightProbe = null;

export function normalizeBaseUrl(raw) {
  const trimmed = String(raw || '').trim().replace(/\/+$/, '');
  if (!trimmed) return DEVICE_SPEECH_DEFAULTS.url;
  return trimmed;
}

export function getDeviceSpeechSettings() {
  const raw = Storage.getJSON(DEVICE_SPEECH_STORAGE_KEY, null);
  const merged = {
    ...DEVICE_SPEECH_DEFAULTS,
    ...(raw && typeof raw === 'object' ? raw : {}),
  };
  merged.enabled = Boolean(merged.enabled);
  merged.url = normalizeBaseUrl(merged.url);
  merged.sttModel = String(merged.sttModel || '').trim() || DEVICE_SPEECH_DEFAULTS.sttModel;
  merged.ttsModel = String(merged.ttsModel || '').trim() || DEVICE_SPEECH_DEFAULTS.ttsModel;
  merged.ttsVoice = String(merged.ttsVoice || '').trim() || DEVICE_SPEECH_DEFAULTS.ttsVoice;
  return merged;
}

export function saveDeviceSpeechSettings(partial) {
  const prev = getDeviceSpeechSettings();
  const next = {
    ...prev,
    ...(partial && typeof partial === 'object' ? partial : {}),
  };
  next.enabled = Boolean(next.enabled);
  next.url = normalizeBaseUrl(next.url);
  next.sttModel = String(next.sttModel ?? prev.sttModel).trim() || DEVICE_SPEECH_DEFAULTS.sttModel;
  next.ttsModel = String(next.ttsModel ?? prev.ttsModel).trim() || DEVICE_SPEECH_DEFAULTS.ttsModel;
  next.ttsVoice = String(next.ttsVoice ?? prev.ttsVoice).trim() || DEVICE_SPEECH_DEFAULTS.ttsVoice;
  Storage.setJSON(DEVICE_SPEECH_STORAGE_KEY, next);
  if (next.url !== prev.url || next.enabled !== prev.enabled) {
    invalidateDeviceProbeCache();
  }
  return next;
}

export function invalidateDeviceProbeCache() {
  _probeCache = { url: '', ok: false, ts: 0, models: [], error: '' };
  _inflightProbe = null;
}

export function hasBrowserSTT() {
  return typeof window !== 'undefined' && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}

export function hasBrowserTTS() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

function _classifyFetchFailure(err) {
  const msg = String((err && err.message) || err || '');
  if (err && err.name === 'AbortError') {
    return 'Timed out (>1 s) — check that Speaches is running on this device.';
  }
  // TypeError: Failed to fetch is what browsers throw on CORS rejection,
  // Chrome Local Network Access block, or connection refused.
  if (err instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(msg)) {
    return 'Blocked or unreachable — check that Speaches is running, CORS ALLOW_ORIGINS includes this origin, and allow any browser Local Network Access prompt.';
  }
  return msg || 'Connection failed';
}

/**
 * Probe `{url}/models` with a ~1 s timeout. Caches result for 60 s unless
 * `force` is true.
 * Returns `{ ok: boolean, models: string[], error?: string, cached?: boolean }`.
 */
export async function probeDeviceServer(options = {}) {
  const cfg = getDeviceSpeechSettings();
  const baseUrl = normalizeBaseUrl(options.url || cfg.url);
  const force = Boolean(options.force);
  const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : PROBE_TIMEOUT_MS;
  const now = Date.now();

  if (
    !force &&
    _probeCache.url === baseUrl &&
    _probeCache.ts > 0 &&
    now - _probeCache.ts < PROBE_CACHE_TTL_MS
  ) {
    return {
      ok: _probeCache.ok,
      models: _probeCache.models.slice(),
      error: _probeCache.error,
      cached: true,
    };
  }

  if (!force && _inflightProbe && _inflightProbe.url === baseUrl) {
    return _inflightProbe.promise;
  }

  const run = (async () => {
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      const res = await fetch(`${baseUrl}/models`, {
        method: 'GET',
        mode: 'cors',
        credentials: 'omit',
        signal: controller ? controller.signal : undefined,
      });
      if (!res.ok) {
        const errMsg = `HTTP ${res.status}`;
        _probeCache = { url: baseUrl, ok: false, ts: Date.now(), models: [], error: errMsg };
        return { ok: false, models: [], error: errMsg, cached: false };
      }
      const data = await res.json().catch(() => ({}));
      const rawList = Array.isArray(data?.data) ? data.data : (Array.isArray(data?.models) ? data.models : []);
      const models = rawList
        .map(m => (typeof m === 'string' ? m : m?.id || m?.name || ''))
        .filter(Boolean);
      _probeCache = { url: baseUrl, ok: true, ts: Date.now(), models, error: '' };
      return { ok: true, models: models.slice(), error: '', cached: false };
    } catch (err) {
      const errMsg = _classifyFetchFailure(err);
      _probeCache = { url: baseUrl, ok: false, ts: Date.now(), models: [], error: errMsg };
      return { ok: false, models: [], error: errMsg, cached: false };
    } finally {
      if (timer) clearTimeout(timer);
      if (_inflightProbe && _inflightProbe.url === baseUrl) {
        _inflightProbe = null;
      }
    }
  })();

  if (!force) {
    _inflightProbe = { url: baseUrl, promise: run };
  }
  return run;
}

/**
 * Direct browser call to device Speaches server:
 * POST {url}/audio/transcriptions (multipart/form-data: file, model, optional language)
 */
export async function transcribeOnDevice(audioBlob, options = {}) {
  const cfg = getDeviceSpeechSettings();
  const baseUrl = normalizeBaseUrl(options.url || cfg.url);
  const model = options.model || cfg.sttModel || DEVICE_SPEECH_DEFAULTS.sttModel;
  const fd = new FormData();
  fd.append('file', audioBlob, 'speech.webm');
  fd.append('model', model);
  if (options.language) {
    fd.append('language', options.language);
  }

  let res;
  try {
    res = await fetch(`${baseUrl}/audio/transcriptions`, {
      method: 'POST',
      mode: 'cors',
      credentials: 'omit',
      body: fd,
    });
  } catch (err) {
    invalidateDeviceProbeCache();
    throw new Error(_classifyFetchFailure(err));
  }

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}));
    const detail = errBody?.error?.message || errBody?.detail?.message || errBody?.detail || `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }

  const data = await res.json();
  return String(data?.text || '').trim();
}

/**
 * Direct browser call to device Speaches server:
 * POST {url}/audio/speech (JSON: model, voice, input, response_format, optional speed)
 * Returns a Blob of audio.
 */
export async function synthesizeOnDevice(text, options = {}) {
  const cfg = getDeviceSpeechSettings();
  const baseUrl = normalizeBaseUrl(options.url || cfg.url);
  const model = options.model || cfg.ttsModel || DEVICE_SPEECH_DEFAULTS.ttsModel;
  const voice = options.voice || cfg.ttsVoice || DEVICE_SPEECH_DEFAULTS.ttsVoice;
  const responseFormat = options.responseFormat || 'mp3';
  const body = {
    model,
    voice,
    input: text,
    response_format: responseFormat,
  };
  if (Number.isFinite(options.speed) && options.speed > 0) {
    body.speed = options.speed;
  }

  let res;
  try {
    res = await fetch(`${baseUrl}/audio/speech`, {
      method: 'POST',
      mode: 'cors',
      credentials: 'omit',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (err) {
    invalidateDeviceProbeCache();
    throw new Error(_classifyFetchFailure(err));
  }

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}));
    const detail = errBody?.error?.message || errBody?.detail?.message || errBody?.detail || `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }

  return res.blob();
}

/**
 * Check if server-side STT is configured (not disabled and not browser-only).
 */
export function isServerSttConfigured(stats) {
  if (!stats || !stats.available) return false;
  const p = String(stats.provider || 'disabled');
  return p === 'local' || p.startsWith('endpoint:');
}

/**
 * Check if server-side TTS is configured (not disabled and not browser-only).
 */
export function isServerTtsConfigured(stats) {
  if (!stats || !stats.available || !stats.ready) return false;
  const p = String(stats.provider || 'disabled');
  return p === 'local' || p.startsWith('endpoint:');
}

/**
 * Resolve which STT backend to use right now:
 *   1. 'device'  — if device speech is enabled and probe succeeds within ~1 s
 *   2. 'server'  — if server STT is configured (local Whisper or endpoint)
 *   3. 'browser' — if browser Web Speech API (SpeechRecognition) is available
 *   4. 'none'    — no STT available
 */
export async function resolveSttBackend(serverStatsOrProvider = null) {
  const cfg = getDeviceSpeechSettings();
  if (cfg.enabled) {
    const probe = await probeDeviceServer();
    if (probe.ok) return 'device';
  }

  if (typeof serverStatsOrProvider === 'string') {
    if (serverStatsOrProvider === 'local' || serverStatsOrProvider.startsWith('endpoint:')) {
      return 'server';
    }
  } else if (serverStatsOrProvider && typeof serverStatsOrProvider === 'object') {
    if (isServerSttConfigured(serverStatsOrProvider)) return 'server';
  } else {
    try {
      const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
      if (res.ok) {
        const stats = await res.json();
        if (isServerSttConfigured(stats)) return 'server';
      }
    } catch (_) { /* ignore */ }
  }

  if (hasBrowserSTT()) return 'browser';
  return 'none';
}

/**
 * Resolve which TTS backend to use right now:
 *   1. 'device'  — if device speech is enabled and probe succeeds within ~1 s
 *   2. 'server'  — if server TTS is configured (local Kokoro or endpoint)
 *   3. 'browser' — if browser speechSynthesis is available
 *   4. 'none'    — no TTS available
 */
export async function resolveTtsBackend(serverStats = null) {
  const cfg = getDeviceSpeechSettings();
  if (cfg.enabled) {
    const probe = await probeDeviceServer();
    if (probe.ok) return 'device';
  }

  let stats = serverStats;
  if (!stats) {
    try {
      const res = await fetch('/api/tts/stats', { credentials: 'same-origin' });
      if (res.ok) stats = await res.json();
    } catch (_) { /* ignore */ }
  }
  if (isServerTtsConfigured(stats)) return 'server';

  if (hasBrowserTTS()) return 'browser';
  return 'none';
}

/**
 * Transcribe an audio Blob with automatic fallback from (a) device server ->
 * (b) Odysseus server `/api/stt/transcribe` (when server STT is configured).
 */
export async function transcribeAudioBlob(audioBlob, { modelEndpointId = '', serverConfigured = null } = {}) {
  const cfg = getDeviceSpeechSettings();
  let lastError = null;

  if (cfg.enabled) {
    const probe = await probeDeviceServer();
    if (probe.ok) {
      try {
        return await transcribeOnDevice(audioBlob);
      } catch (err) {
        lastError = err;
        console.warn('[deviceSpeech] Device STT failed, falling back:', err.message);
      }
    }
  }

  let tryServer = serverConfigured;
  if (tryServer === null) {
    try {
      const sRes = await fetch('/api/stt/stats', { credentials: 'same-origin' });
      if (sRes.ok) {
        const stats = await sRes.json();
        tryServer = isServerSttConfigured(stats);
      }
    } catch (_) {
      tryServer = false;
    }
  }

  if (tryServer) {
    const fd = new FormData();
    fd.append('file', audioBlob, 'speech.webm');
    if (modelEndpointId) fd.append('model_endpoint_id', modelEndpointId);
    const res = await fetch('/api/stt/transcribe', {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err.detail && err.detail.message) || `HTTP ${res.status}`);
    }
    const data = await res.json();
    return String(data.text || '').trim();
  }

  if (lastError) throw lastError;
  throw new Error('Speech-to-text is not available on this device or server');
}

const deviceSpeech = {
  DEVICE_SPEECH_STORAGE_KEY,
  DEVICE_SPEECH_DEFAULTS,
  PROBE_TIMEOUT_MS,
  PROBE_CACHE_TTL_MS,
  normalizeBaseUrl,
  getDeviceSpeechSettings,
  saveDeviceSpeechSettings,
  invalidateDeviceProbeCache,
  hasBrowserSTT,
  hasBrowserTTS,
  probeDeviceServer,
  transcribeOnDevice,
  synthesizeOnDevice,
  isServerSttConfigured,
  isServerTtsConfigured,
  resolveSttBackend,
  resolveTtsBackend,
  transcribeAudioBlob,
};

if (typeof window !== 'undefined') {
  window.deviceSpeech = deviceSpeech;
}

export default deviceSpeech;
