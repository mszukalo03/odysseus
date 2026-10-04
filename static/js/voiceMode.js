// Live voice mode: a hands-free spoken conversation with the current chat.
//
// Loop: listen (voice activity detection on the mic) -> when you stop
// talking, transcribe on the server (/api/stt/transcribe) -> send the text as
// a normal chat message flagged voice=true (the server adds speech-style
// instructions and turns thinking off unless the composer chose a level) ->
// the reply is spoken sentence by sentence while it streams (tts-ai.js) ->
// when it has finished speaking, listen again. Talking over the reply
// (barge-in) stops it and starts a new turn. Esc or the End button exits.
//
// VAD is a simple adaptive energy detector on a Web Audio AnalyserNode: no
// model download, works offline. Sensitivity, end-of-speech silence,
// barge-in and the "one moment" filler are per-browser settings (gear in the
// voice bar). The mic needs a secure context (HTTPS or localhost).

import Storage from './storage.js';

const SETTINGS_KEY = 'voiceModeSettings';
const DEFAULTS = {
  silenceMs: 900,      // this much quiet ends an utterance
  sensitivity: 3,      // speech = energy above noise floor x this (lower = more sensitive)
  bargeIn: true,       // talking over the reply interrupts it
  filler: true,        // say "One moment." when the agent starts using tools
};
const MIN_SPEECH_MS = 300;        // shorter blips are ignored
const START_SPEECH_MS = 120;      // sustained energy needed to count as speech
const BARGE_IN_MS = 350;          // sustained speech needed to interrupt playback
const MAX_UTTERANCE_MS = 60000;   // hard cap on one recording
const IDLE_RESTART_MS = 30000;    // restart an idle recorder so blobs stay small
const MIN_RMS = 0.012;            // absolute floor for "speech" energy
// Whisper's usual hallucinations on silence/noise -- drop these transcripts.
const NOISE_TRANSCRIPTS = /^(you|thank you\.?|thanks( for watching)?\.?|bye\.?|\.+|uh+|um+|hmm+)$/i;

const STATE_LABELS = {
  off: '',
  starting: 'Starting microphone…',
  listening: 'Listening',
  hearing: 'Listening…',
  transcribing: 'Transcribing…',
  waiting: 'Thinking…',
  speaking: 'Speaking',
};

let state = 'off';
let settings = { ...DEFAULTS };
let stream = null;
let audioCtx = null;
let analyser = null;
let sampleBuf = null;
let vadTimer = null;
let recorder = null;
let chunks = [];
let noiseFloor = 0.01;
let speechMs = 0;
let silenceMs = 0;
let utteranceMs = 0;
let heardSpeech = false;
let bargeMs = 0;
let fillerSaid = false;
let replyStreaming = false;   // our message's reply is still streaming in
let savedTTS = null;
let unsubTTS = [];
let bar = null;

function _loadSettings() {
  const raw = Storage.getToggle(SETTINGS_KEY, null);
  settings = { ...DEFAULTS, ...(raw && typeof raw === 'object' ? raw : {}) };
}

function _saveSettings() {
  Storage.setToggle(SETTINGS_KEY, settings);
}

function _toast(msg, isError) {
  const ui = window.uiModule;
  if (isError && ui && ui.showError) ui.showError(msg);
  else if (ui && ui.showToast) ui.showToast(msg);
  else console.log('[voice]', msg);
}

function _tts() {
  return window.aiTTSManager || null;
}

// The chat's model endpoint (id, or base URL when the id isn't known), so the
// server can route speech to the box the model runs on.
function _chatEndpointRef() {
  try {
    const picked = window.__odysseusLastPickedRoute;
    if (picked && picked.endpoint_id) return picked.endpoint_id;
    const sm = window.sessionModule;
    const url = sm && sm.getCurrentEndpointUrl ? sm.getCurrentEndpointUrl() : '';
    return url || (picked && picked.endpoint_url) || '';
  } catch (_) {
    return '';
  }
}

// ── UI ──────────────────────────────────────────────────────────────────

const ICON_MIC = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>';
const ICON_GEAR = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';

function _buildBar() {
  if (bar) return bar;
  bar = document.createElement('div');
  bar.className = 'voice-mode-bar';
  bar.setAttribute('role', 'status');
  bar.setAttribute('aria-live', 'polite');
  bar.hidden = true;
  bar.innerHTML = `
    <span class="voice-mode-dot" aria-hidden="true"></span>
    <span class="voice-mode-icon" aria-hidden="true">${ICON_MIC}</span>
    <span class="voice-mode-label"></span>
    <span class="voice-mode-meter" aria-hidden="true"><span class="voice-mode-meter-fill"></span></span>
    <button type="button" class="voice-mode-gear" title="Voice settings" aria-label="Voice settings">${ICON_GEAR}</button>
    <button type="button" class="voice-mode-end" title="End voice mode (Esc)">End</button>
    <div class="voice-mode-settings" hidden>
      <label>Sensitivity
        <input type="range" min="1.5" max="6" step="0.5" data-k="sensitivity">
        <span class="voice-mode-hint">left = picks up quieter speech</span>
      </label>
      <label>Pause before sending (ms)
        <input type="number" min="400" max="3000" step="100" data-k="silenceMs">
      </label>
      <label class="voice-mode-check"><input type="checkbox" data-k="bargeIn"> Talk over the reply to interrupt it</label>
      <label class="voice-mode-check"><input type="checkbox" data-k="filler"> Say "One moment" while tools run</label>
    </div>`;
  document.body.appendChild(bar);
  bar.querySelector('.voice-mode-end').addEventListener('click', () => stop());
  const panel = bar.querySelector('.voice-mode-settings');
  bar.querySelector('.voice-mode-gear').addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) _fillSettings(panel);
  });
  panel.addEventListener('change', (e) => {
    const k = e.target && e.target.dataset && e.target.dataset.k;
    if (!k) return;
    if (e.target.type === 'checkbox') settings[k] = e.target.checked;
    else {
      const v = parseFloat(e.target.value);
      if (Number.isFinite(v)) settings[k] = v;
    }
    _saveSettings();
  });
  return bar;
}

function _fillSettings(panel) {
  panel.querySelectorAll('[data-k]').forEach(input => {
    const v = settings[input.dataset.k];
    if (input.type === 'checkbox') input.checked = !!v;
    else input.value = String(v);
  });
}

function _setState(next) {
  state = next;
  if (bar) {
    bar.dataset.state = next;
    bar.querySelector('.voice-mode-label').textContent = STATE_LABELS[next] || '';
  }
  const btn = document.getElementById('voice-mode-btn');
  if (btn) {
    btn.classList.toggle('active', next !== 'off');
    btn.setAttribute('aria-pressed', next !== 'off' ? 'true' : 'false');
  }
}

function _meter(level) {
  if (!bar) return;
  const fill = bar.querySelector('.voice-mode-meter-fill');
  if (fill) fill.style.width = `${Math.min(100, Math.round(level * 900))}%`;
}

// ── Audio capture + VAD ─────────────────────────────────────────────────

function _rms() {
  analyser.getFloatTimeDomainData(sampleBuf);
  let sum = 0;
  for (let i = 0; i < sampleBuf.length; i++) sum += sampleBuf[i] * sampleBuf[i];
  return Math.sqrt(sum / sampleBuf.length);
}

function _startRecorder() {
  chunks = [];
  heardSpeech = false;
  speechMs = 0;
  silenceMs = 0;
  utteranceMs = 0;
  try {
    const mime = MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus' : 'audio/webm';
    recorder = new MediaRecorder(stream, { mimeType: mime });
  } catch (_) {
    recorder = new MediaRecorder(stream);
  }
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  recorder.start(250);
}

function _stopRecorder() {
  return new Promise((resolve) => {
    if (!recorder || recorder.state === 'inactive') {
      resolve(new Blob(chunks, { type: 'audio/webm' }));
      return;
    }
    recorder.onstop = () => resolve(new Blob(chunks, { type: 'audio/webm' }));
    try { recorder.stop(); } catch (_) { resolve(new Blob(chunks, { type: 'audio/webm' })); }
  });
}

function _tick() {
  if (state === 'off' || !analyser) return;
  const step = 50;
  const level = _rms();
  _meter(level);
  const threshold = Math.max(MIN_RMS, noiseFloor * settings.sensitivity);
  const loud = level > threshold;

  if (state === 'listening' || state === 'hearing') {
    utteranceMs += step;
    if (loud) {
      speechMs += step;
      silenceMs = 0;
      if (!heardSpeech && speechMs >= START_SPEECH_MS) {
        heardSpeech = true;
        _setState('hearing');
      }
    } else {
      // Track the room's noise floor only while nobody is talking.
      if (!heardSpeech) noiseFloor = noiseFloor * 0.95 + level * 0.05;
      silenceMs += step;
      if (!heardSpeech) speechMs = Math.max(0, speechMs - step);
    }
    if (heardSpeech && (silenceMs >= settings.silenceMs || utteranceMs >= MAX_UTTERANCE_MS)) {
      _finishUtterance();
    } else if (!heardSpeech && utteranceMs >= IDLE_RESTART_MS) {
      _stopRecorder().then(() => { if (state === 'listening') _startRecorder(); });
      utteranceMs = 0;
    }
    return;
  }

  // While the reply is pending or playing: barge-in detection only. Echo
  // cancellation removes most of the speaker output; the higher bar covers
  // what leaks through.
  if ((state === 'speaking' || state === 'waiting') && settings.bargeIn) {
    if (level > threshold * 1.6) {
      bargeMs += step;
      if (bargeMs >= BARGE_IN_MS) _bargeIn();
    } else {
      bargeMs = Math.max(0, bargeMs - step);
    }
  }
}

async function _finishUtterance() {
  const enough = speechMs >= MIN_SPEECH_MS;
  _setState('transcribing');
  const blob = await _stopRecorder();
  if (state !== 'transcribing') return;
  if (!enough || blob.size < 1000) {
    _listen();
    return;
  }
  let text = '';
  try {
    const fd = new FormData();
    fd.append('file', blob, 'speech.webm');
    const ref = _chatEndpointRef();
    if (ref) fd.append('model_endpoint_id', ref);
    const res = await fetch('/api/stt/transcribe', { method: 'POST', credentials: 'same-origin', body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err.detail && err.detail.message) || `HTTP ${res.status}`);
    }
    text = ((await res.json()).text || '').trim();
  } catch (e) {
    if (state === 'off') return;
    _toast('Transcription failed: ' + e.message, true);
    _listen();
    return;
  }
  if (state !== 'transcribing') return;
  if (!text || NOISE_TRANSCRIPTS.test(text)) {
    _listen();
    return;
  }
  _send(text);
}

function _send(text) {
  const input = document.getElementById('message');
  const form = document.getElementById('chat-form');
  if (!input || !form) {
    _listen();
    return;
  }
  fillerSaid = false;
  bargeMs = 0;
  const tts = _tts();
  if (tts) tts.modelEndpointId = _chatEndpointRef() || null;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  replyStreaming = true;
  _setState('waiting');
  if (form.requestSubmit) form.requestSubmit();
  else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

function _listen() {
  if (state === 'off') return;
  bargeMs = 0;
  _setState('listening');
  try {
    _startRecorder();
  } catch (e) {
    _toast('Could not record from the microphone: ' + (e && e.message), true);
    stop();
  }
}

function _bargeIn() {
  bargeMs = 0;
  const tts = _tts();
  if (tts) tts.stop();
  const chat = window.chatModule;
  if (replyStreaming && chat && chat.abortCurrentRequest) {
    replyStreaming = false;
    try { chat.abortCurrentRequest(true); } catch (_) { /* ignore */ }
  }
  _listen();
}

// ── Chat / TTS events ───────────────────────────────────────────────────

function _onStreamEnd() {
  replyStreaming = false;
  if (state !== 'waiting' && state !== 'speaking') return;
  const tts = _tts();
  // Still speaking the tail of the reply: re-arm when the queue drains.
  if (tts && (tts._processing || tts.isPlaying || (tts._queue && tts._queue.length))) {
    _setState('speaking');
    return;
  }
  _listen();
}

function _onToolStart() {
  if (state !== 'waiting' || !settings.filler || fillerSaid) return;
  const tts = _tts();
  if (!tts || tts._processing || tts.isPlaying) return;
  fillerSaid = true;
  const btn = document.createElement('button');
  btn.style.display = 'none';
  tts.enqueue('One moment.', btn, () => {});
}

function _onKey(e) {
  if (e.key === 'Escape' && state !== 'off') {
    e.preventDefault();
    stop();
  }
}

// ── Public API ──────────────────────────────────────────────────────────

export function isActive() {
  return state !== 'off';
}

export async function start() {
  if (state !== 'off') return;
  _loadSettings();
  if (!window.isSecureContext) {
    _toast('Voice mode needs HTTPS (or localhost) for the microphone.', true);
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
    _toast('This browser cannot record audio.', true);
    return;
  }
  try {
    const stats = await fetch('/api/stt/stats', { credentials: 'same-origin' }).then(r => r.json());
    if (!stats.available || stats.provider === 'browser' || stats.provider === 'disabled') {
      _toast('Voice mode needs server speech-to-text (Settings: STT provider local or an endpoint).', true);
      return;
    }
  } catch (_) { /* let the first transcription report problems */ }
  const tts = _tts();
  if (tts && tts.checkAvailability) await tts.checkAvailability();
  if (!tts || !tts.available) {
    _toast('Voice mode needs text-to-speech to be configured.', true);
    return;
  }

  _buildBar();
  bar.hidden = false;
  _setState('starting');
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    _setState('off');
    bar.hidden = true;
    _toast(e && e.name === 'NotAllowedError' ? 'Microphone access denied.' : 'Microphone error: ' + (e && e.message), true);
    return;
  }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  audioCtx = new Ctx();
  const source = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  sampleBuf = new Float32Array(analyser.fftSize);
  source.connect(analyser);
  noiseFloor = 0.01;

  // Speak replies while they stream, with voice-friendly tuning; restored on stop.
  savedTTS = {
    autoPlay: tts.autoPlay, minSentenceChars: tts.minSentenceChars,
    earlyFlush: tts.earlyFlush, prefetch: tts.prefetch, modelEndpointId: tts.modelEndpointId,
  };
  tts.autoPlay = true;
  tts.minSentenceChars = 2;
  tts.earlyFlush = true;
  tts.prefetch = true;
  unsubTTS = [
    tts.on('playbackstart', () => { if (state === 'waiting') _setState('speaking'); }),
    tts.on('queuedrained', () => {
      if (state === 'speaking' && !replyStreaming) _listen();
    }),
  ];
  document.addEventListener('odysseus:chat-stream-end', _onStreamEnd);
  document.addEventListener('odysseus:agent-tool-start', _onToolStart);
  document.addEventListener('keydown', _onKey, true);

  vadTimer = setInterval(_tick, 50);
  _listen();
}

export function stop() {
  if (state === 'off') return;
  _setState('off');
  if (vadTimer) clearInterval(vadTimer);
  vadTimer = null;
  try { if (recorder && recorder.state !== 'inactive') recorder.stop(); } catch (_) { /* ignore */ }
  recorder = null;
  if (stream) stream.getTracks().forEach(t => t.stop());
  stream = null;
  if (audioCtx) audioCtx.close().catch(() => {});
  audioCtx = null;
  analyser = null;
  document.removeEventListener('odysseus:chat-stream-end', _onStreamEnd);
  document.removeEventListener('odysseus:agent-tool-start', _onToolStart);
  document.removeEventListener('keydown', _onKey, true);
  unsubTTS.forEach(fn => fn());
  unsubTTS = [];
  const tts = _tts();
  if (tts) {
    tts.stop();
    if (savedTTS) Object.assign(tts, savedTTS);
  }
  savedTTS = null;
  if (bar) {
    bar.hidden = true;
    const panel = bar.querySelector('.voice-mode-settings');
    if (panel) panel.hidden = true;
  }
}

export function toggle() {
  if (isActive()) stop();
  else start();
}

function init() {
  const btn = document.getElementById('voice-mode-btn');
  if (btn && !btn.dataset.voiceInit) {
    btn.dataset.voiceInit = '1';
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      toggle();
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

const voiceMode = { start, stop, toggle, isActive, init };
window.voiceMode = voiceMode;
export default voiceMode;
