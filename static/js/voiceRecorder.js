// static/js/voiceRecorder.js

/**
 * Voice recording with optional Speech-to-Text transcription.
 *
 * Resolution order for each STT call:
 *   (a) Device server (Speaches / OpenAI-compatible on localhost) if enabled
 *       and /v1/models responds within ~1 s (cached for 60 s).
 *   (b) Odysseus server (/api/stt/transcribe) if server STT is configured.
 *   (c) Browser fallback: Web Speech API (SpeechRecognition / webkitSpeechRecognition).
 */

import {
  getDeviceSpeechSettings,
  probeDeviceServer,
  transcribeOnDevice,
  hasBrowserSTT,
} from './deviceSpeech.js';

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = null;
let recordingInterval = null;

// Browser STT state
let _recognition = null;
let _browserTranscript = '';

// Cached STT provider — refreshed on settings change
let _sttProvider = 'disabled';
let _serverSttAvailable = false;
let _serverSttProvider = 'disabled';

function _computeEffectiveProvider() {
  const devCfg = getDeviceSpeechSettings();
  if (devCfg.enabled) return 'device';
  if (_serverSttAvailable && _serverSttProvider && _serverSttProvider !== 'disabled') {
    return _serverSttProvider;
  }
  if (hasBrowserSTT()) return 'browser';
  return 'disabled';
}

/**
 * Fetch current STT provider from server settings and combine with device/browser fallback
 */
async function refreshSttProvider() {
  try {
    const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (res.ok) {
      const stats = await res.json();
      _serverSttAvailable = Boolean(stats.available);
      _serverSttProvider = stats.provider || 'disabled';
    }
  } catch (e) {
    console.warn('Failed to fetch STT stats:', e);
  }
  _sttProvider = _computeEffectiveProvider();
  // Notify the send button to update its icon
  if (window._updateSendBtnIcon) window._updateSendBtnIcon();
}

/**
 * Format seconds as MM:SS
 */
function formatTime(seconds) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

/**
 * Reset UI state after recording ends
 */
function _resetRecordingUI() {
  isRecording = false;
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  // Reset send button via global callback
  const sendBtn = document.querySelector('.send-btn');
  if (sendBtn) {
    sendBtn.classList.remove('recording');
    sendBtn.dataset.mode = '';
  }
  if (window._updateSendBtnIcon) {
    setTimeout(window._updateSendBtnIcon, 50);
  }
}

/**
 * Start browser speech recognition alongside recording
 */
function startBrowserSTT() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

  _browserTranscript = '';
  _recognition = new SpeechRecognition();
  _recognition.continuous = true;
  _recognition.interimResults = false;
  _recognition.lang = '';

  _recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        _browserTranscript += event.results[i][0].transcript + ' ';
      }
    }
  };

  _recognition.onerror = (e) => {
    console.warn('Browser STT error:', e.error);
  };

  try {
    _recognition.start();
  } catch (e) {
    console.warn('Browser STT start failed:', e);
  }
}

function stopBrowserSTT() {
  if (_recognition) {
    try { _recognition.stop(); } catch (e) { /* ignore */ }
    _recognition = null;
  }
  return _browserTranscript.trim();
}

/**
 * Send audio to server for transcription
 */
async function transcribeOnServer(audioBlob) {
  const formData = new FormData();
  formData.append('file', audioBlob, 'audio.webm');

  const res = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.message || 'Transcription failed');
  }

  const data = await res.json();
  return data.text || '';
}

/**
 * Insert transcribed text into the chat input
 */
function insertTranscription(text, showToast) {
  if (!text) return;
  const input = document.getElementById('message');
  if (!input) return;

  const existing = input.value.trim();
  input.value = existing ? existing + ' ' + text : text;

  // Trigger auto-resize and icon update
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  if (showToast) showToast('Transcribed');
}

/**
 * Resolve and transcribe a recorded audio blob following the 3-tier order:
 * (a) device server if enabled & probe succeeds -> (b) Odysseus server if configured -> (c) browser transcript
 */
export async function transcribeWithFallback(audioBlob, browserTranscript = '') {
  const devCfg = getDeviceSpeechSettings();
  let lastErr = null;

  // (a) Device server
  if (devCfg.enabled) {
    const probe = await probeDeviceServer();
    if (probe.ok) {
      try {
        return await transcribeOnDevice(audioBlob);
      } catch (e) {
        lastErr = e;
        console.warn('Device STT failed, falling through:', e);
      }
    }
  }

  // (b) Odysseus server routes if configured
  const serverConfigured =
    (_serverSttAvailable && (_serverSttProvider === 'local' || _serverSttProvider.startsWith('endpoint:'))) ||
    (_sttProvider === 'local' || _sttProvider.startsWith('endpoint:'));
  if (serverConfigured) {
    try {
      return await transcribeOnServer(audioBlob);
    } catch (e) {
      lastErr = e;
      console.warn('Server STT failed, falling through:', e);
    }
  }

  // (c) Browser fallback
  if (browserTranscript) {
    return browserTranscript;
  }
  if (hasBrowserSTT()) {
    return '';
  }

  if (lastErr) throw lastErr;
  return null;
}

/**
 * Start voice recording
 */
export function startRecording(onFileCreated, showToast, showError) {
  // Check for secure context (getUserMedia requires HTTPS or localhost)
  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.');
    _resetRecordingUI();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    _resetRecordingUI();
    return;
  }

  audioChunks = [];

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const browserText = stopBrowserSTT();
        const provider = _sttProvider;

        if (provider === 'disabled' && !getDeviceSpeechSettings().enabled) {
          // STT explicitly disabled — attach audio file
          const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
          if (onFileCreated) onFileCreated(audioFile);
          _resetRecordingUI();
          return;
        }

        if (provider !== 'browser' || getDeviceSpeechSettings().enabled) {
          if (showToast) showToast('Transcribing...', 5000);
        }

        try {
          const transcript = await transcribeWithFallback(audioBlob, browserText);
          if (transcript) {
            insertTranscription(transcript, showToast);
          } else {
            if (showToast) showToast('No speech detected');
            if (provider === 'browser') {
              const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
              if (onFileCreated) onFileCreated(audioFile);
            }
          }
        } catch (e) {
          console.error('STT transcription error:', e);
          if (showError) showError('Transcription failed: ' + e.message);
          const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
          if (onFileCreated) onFileCreated(audioFile);
        }

        _resetRecordingUI();
      };

      mediaRecorder.start();
      isRecording = true;
      recordingStartTime = new Date();

      // Start browser STT alongside recording when available so it can serve
      // as the primary or fallback transcript.
      if (hasBrowserSTT()) {
        startBrowserSTT();
      }

      if (showToast) {
        showToast('Recording...');
      }
    })
    .catch(error => {
      console.error('Microphone access error:', error);
      if (showError) {
        if (error.name === 'NotAllowedError') {
          showError('Microphone access denied. Check browser permissions.');
        } else if (error.name === 'NotFoundError') {
          showError('No microphone found.');
        } else {
          showError('Microphone error: ' + error.message);
        }
      }
      _resetRecordingUI();
    });
}

/**
 * Stop voice recording
 */
export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    // isRecording will be set to false in _resetRecordingUI called from onstop
  } else {
    _resetRecordingUI();
  }
}

/**
 * Check if currently recording
 */
export function getIsRecording() {
  return isRecording;
}

/**
 * Initialize recording state
 */
export function init() {
  isRecording = false;
  refreshSttProvider();
}

const voiceRecorderModule = {
  startRecording,
  stopRecording,
  getIsRecording,
  init,
  refreshSttProvider,
  transcribeWithFallback,
  get _sttProvider() { return _sttProvider; },
  set _sttProvider(v) { _sttProvider = v; },
};

if (typeof window !== 'undefined') {
  window.voiceRecorderModule = voiceRecorderModule;
}

export default voiceRecorderModule;
