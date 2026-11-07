import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const JS = path.join(REPO, 'static/js');

const store = new Map();
let fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({}) });
let fetchCalls = [];
let spokenUtterances = [];

function makeContext() {
  const docListeners = new Map();
  const elementsById = new Map();

  function makeEl(tag = 'div') {
    const listeners = new Map();
    const children = [];
    const el = {
      tagName: tag.toUpperCase(),
      id: '',
      className: '',
      hidden: false,
      disabled: false,
      checked: false,
      value: '',
      textContent: '',
      innerText: '',
      _innerHTML: '',
      dataset: {},
      style: {},
      children,
      classList: {
        _set: new Set(),
        add(...n) { n.forEach(x => this._set.add(x)); },
        remove(...n) { n.forEach(x => this._set.delete(x)); },
        contains(n) { return this._set.has(n); },
        toggle(n, f) {
          const on = f === undefined ? !this._set.has(n) : Boolean(f);
          on ? this._set.add(n) : this._set.delete(n);
          return on;
        },
      },
      setAttribute(k, v) { this[k] = v; },
      getAttribute(k) { return this[k] ?? null; },
      appendChild(c) { children.push(c); return c; },
      querySelector() { return makeEl(); },
      querySelectorAll() { return []; },
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
      },
      removeEventListener() {},
      dispatchEvent(ev) {
        for (const fn of listeners.get(ev.type) || []) fn(ev);
      },
    };
    Object.defineProperty(el, 'innerHTML', {
      get() { return this._innerHTML; },
      set(v) {
        this._innerHTML = String(v || '');
        // Simple HTML strip for extractPlainText
        this.textContent = this._innerHTML.replace(/<[^>]+>/g, '');
      },
    });
    return el;
  }

  const document = {
    readyState: 'complete',
    body: makeEl('body'),
    createElement(tag) { return makeEl(tag); },
    getElementById(id) {
      if (!elementsById.has(id)) {
        const el = makeEl('div');
        el.id = id;
        elementsById.set(id, el);
      }
      return elementsById.get(id);
    },
    querySelector() { return null; },
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
    removeEventListener() {},
  };

  class FakeFormData {
    constructor() { this.entries = []; }
    append(k, v, name) { this.entries.push({ k, v, name }); }
    get(k) { const found = this.entries.find(e => e.k === k); return found ? found.v : null; }
  }

  class FakeBlob {
    constructor(parts = [], opts = {}) {
      this.parts = parts;
      this.type = opts.type || '';
      this.size = parts.reduce((acc, p) => acc + (p.length || p.size || 10), 0) || 2048;
    }
  }

  class FakeFile extends FakeBlob {
    constructor(parts, name, opts) {
      super(parts, opts);
      this.name = name;
    }
  }

  class FakeSpeechSynthesisUtterance {
    constructor(text) {
      this.text = text;
      this.rate = 1;
      this.voice = null;
      this.onstart = null;
      this.onend = null;
      this.onerror = null;
    }
  }

  class FakeAudio {
    constructor(url) {
      this.src = url;
      this.playbackRate = 1;
      this.currentTime = 0;
      this.onended = null;
      this.onerror = null;
      this.onpause = null;
    }
    async play() {
      if (this.onended) setTimeout(() => this.onended(), 0);
    }
    pause() {
      if (this.onpause) this.onpause();
    }
  }

  const sandbox = {
    console,
    document,
    localStorage: {
      getItem(k) { return store.has(k) ? store.get(k) : null; },
      setItem(k, v) { store.set(k, String(v)); },
      removeItem(k) { store.delete(k); },
    },
    fetch: async (url, opts = {}) => {
      fetchCalls.push({ url: String(url), opts });
      return fetchHandler(String(url), opts);
    },
    FormData: FakeFormData,
    Blob: FakeBlob,
    File: FakeFile,
    Audio: FakeAudio,
    SpeechSynthesisUtterance: FakeSpeechSynthesisUtterance,
    speechSynthesis: {
      getVoices() { return [{ name: 'Default Browser Voice' }]; },
      speak(utt) {
        spokenUtterances.push(utt.text);
        if (utt.onstart) utt.onstart();
        if (utt.onend) utt.onend();
      },
      cancel() {},
    },
    SpeechRecognition: class {
      start() {}
      stop() {}
    },
    URL: {
      createObjectURL() { return 'blob:fake-audio-url'; },
      revokeObjectURL() {},
    },
    AbortController,
    Event: class { constructor(type, opts = {}) { this.type = type; Object.assign(this, opts); } },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Date,
    Math,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Map,
    Set,
    Promise,
    Error,
    TypeError,
    RegExp,
    JSON,
    isSecureContext: true,
    addEventListener() {},
    removeEventListener() {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  return vm.createContext(sandbox);
}

const context = makeContext();
const moduleCache = new Map();

async function loadEsm(filePath) {
  const resolved = path.resolve(filePath);
  if (moduleCache.has(resolved)) return moduleCache.get(resolved);

  if (resolved === path.join(JS, 'appConfig.js')) {
    const syn = new vm.SyntheticModule(['getSettings', 'invalidateSettings'], function() {
      this.setExport('getSettings', async () => ({ tts_enabled: true }));
      this.setExport('invalidateSettings', () => {});
    }, { context, identifier: resolved });
    moduleCache.set(resolved, syn);
    return syn;
  }

  if (resolved === path.join(JS, 'settings/api.js')) {
    const syn = new vm.SyntheticModule(['postSettings'], function() {
      this.setExport('postSettings', async () => ({ ok: true, status: 200 }));
    }, { context, identifier: resolved });
    moduleCache.set(resolved, syn);
    return syn;
  }

  const code = fs.readFileSync(resolved, 'utf8');
  const mod = new vm.SourceTextModule(code, { context, identifier: resolved });
  moduleCache.set(resolved, mod);
  await mod.link(async (spec, ref) => {
    const clean = spec.split('?')[0];
    return loadEsm(path.resolve(path.dirname(ref.identifier), clean));
  });
  await mod.evaluate();
  return mod;
}

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, pass: true });
  } catch (err) {
    results.push({ name, pass: false, detail: err && err.stack ? err.stack : String(err) });
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'Assertion failed');
}

const devSpeechMod = await loadEsm(path.join(JS, 'deviceSpeech.js'));
const voiceRecorderMod = await loadEsm(path.join(JS, 'voiceRecorder.js'));
const voiceModeMod = await loadEsm(path.join(JS, 'voiceMode.js'));
const ttsMod = await loadEsm(path.join(JS, 'tts-ai.js'));
const speechSettingsMod = await loadEsm(path.join(JS, 'settings/speech.js'));

const ds = devSpeechMod.namespace;
const vr = voiceRecorderMod.namespace;
const vmMod = voiceModeMod.namespace;
const { AITTSManager } = ttsMod.namespace;

// 1. Default settings & localStorage persistence
await check('defaults_and_local_storage_persistence', async () => {
  store.clear();
  const def = ds.getDeviceSpeechSettings();
  assert(def.enabled === false, 'default enabled should be false');
  assert(def.url === 'http://localhost:8000/v1', `unexpected default url: ${def.url}`);
  assert(def.sttModel === 'Systran/faster-distil-whisper-small.en', `unexpected sttModel: ${def.sttModel}`);
  assert(def.ttsModel === 'speaches-ai/Kokoro-82M-v1.0-ONNX', `unexpected ttsModel: ${def.ttsModel}`);
  assert(def.ttsVoice === 'af_heart', `unexpected ttsVoice: ${def.ttsVoice}`);

  ds.saveDeviceSpeechSettings({
    enabled: true,
    url: 'http://127.0.0.1:8000/v1///',
    sttModel: 'Systran/faster-whisper-large-v3-turbo',
  });
  const saved = ds.getDeviceSpeechSettings();
  assert(saved.enabled === true, 'saved enabled should be true');
  assert(saved.url === 'http://127.0.0.1:8000/v1', `trailing slashes not stripped: ${saved.url}`);
  assert(saved.sttModel === 'Systran/faster-whisper-large-v3-turbo', 'custom sttModel not saved');
});

// 2. Probe caching (60 s TTL) and force-bypass
await check('probe_caching_and_force_refresh', async () => {
  ds.invalidateDeviceProbeCache();
  ds.saveDeviceSpeechSettings({ enabled: true, url: 'http://localhost:8000/v1' });
  fetchCalls = [];
  fetchHandler = async (url) => {
    if (url === 'http://localhost:8000/v1/models') {
      return {
        ok: true,
        status: 200,
        json: async () => ({ data: [{ id: 'Systran/faster-distil-whisper-small.en' }, { id: 'speaches-ai/Kokoro-82M-v1.0-ONNX' }] }),
      };
    }
    throw new Error('unexpected url ' + url);
  };

  const first = await ds.probeDeviceServer();
  assert(first.ok === true && first.cached === false, 'first probe should hit network');
  assert(first.models.length === 2, 'should parse model ids');
  assert(fetchCalls.length === 1, 'expected 1 fetch call');

  const second = await ds.probeDeviceServer();
  assert(second.ok === true && second.cached === true, 'second probe within 60s should be cached');
  assert(fetchCalls.length === 1, 'cached probe should not fetch again');

  const forced = await ds.probeDeviceServer({ force: true });
  assert(forced.ok === true && forced.cached === false, 'forced probe should bypass cache');
  assert(fetchCalls.length === 2, 'forced probe should trigger 2nd fetch');
});

// 3. Probe timeout (~1s) and LNA/CORS blocked fetch hint
await check('probe_timeout_and_cors_lna_hint', async () => {
  ds.invalidateDeviceProbeCache();
  fetchHandler = async (_url, opts) => {
    return new Promise((_resolve, reject) => {
      if (opts && opts.signal) {
        opts.signal.addEventListener('abort', () => {
          const err = new Error('The operation was aborted');
          err.name = 'AbortError';
          reject(err);
        });
      }
    });
  };
  const timedOut = await ds.probeDeviceServer({ timeoutMs: 30, force: true });
  assert(timedOut.ok === false, 'timed out probe should return ok: false');
  assert(/Timed out/i.test(timedOut.error), `expected timeout message, got: ${timedOut.error}`);

  fetchHandler = async () => {
    throw new TypeError('Failed to fetch');
  };
  const blocked = await ds.probeDeviceServer({ force: true });
  assert(blocked.ok === false, 'blocked probe should return ok: false');
  assert(/ALLOW_ORIGINS/i.test(blocked.error) && /Local Network Access/i.test(blocked.error),
    `expected CORS + LNA hint, got: ${blocked.error}`);
});

// 4. STT resolution order: (a) device -> (b) server -> (c) browser
await check('stt_resolution_order_device_then_server_then_browser', async () => {
  ds.invalidateDeviceProbeCache();
  ds.saveDeviceSpeechSettings({
    enabled: true,
    url: 'http://localhost:8000/v1',
    sttModel: 'Systran/faster-distil-whisper-small.en',
  });
  fetchCalls = [];
  fetchHandler = async (url, opts) => {
    if (url === 'http://localhost:8000/v1/models') {
      return { ok: true, status: 200, json: async () => ({ data: [] }) };
    }
    if (url === 'http://localhost:8000/v1/audio/transcriptions') {
      const model = opts.body.get('model');
      assert(model === 'Systran/faster-distil-whisper-small.en', `wrong model sent: ${model}`);
      return { ok: true, status: 200, json: async () => ({ text: 'from device speaches' }) };
    }
    throw new Error('unexpected url: ' + url);
  };

  const blob = new context.Blob(['audio'], { type: 'audio/webm' });
  const textA = await vr.transcribeWithFallback(blob, 'from browser');
  assert(textA === 'from device speaches', `expected device transcript, got: ${textA}`);

  // Now make device unreachable -> should fall through to (b) server when configured
  ds.invalidateDeviceProbeCache();
  vr.default._sttProvider = 'local';
  fetchHandler = async (url) => {
    if (url === 'http://localhost:8000/v1/models') {
      throw new TypeError('Failed to fetch');
    }
    if (url === '/api/stt/transcribe') {
      return { ok: true, status: 200, json: async () => ({ text: 'from odysseus server' }) };
    }
    throw new Error('unexpected url: ' + url);
  };
  const textB = await vr.transcribeWithFallback(blob, 'from browser');
  assert(textB === 'from odysseus server', `expected server transcript, got: ${textB}`);

  // Now server is unconfigured -> should fall through to (c) browser
  ds.invalidateDeviceProbeCache();
  vr.default._sttProvider = 'browser';
  const textC = await vr.transcribeWithFallback(blob, 'from browser speech recognition');
  assert(textC === 'from browser speech recognition', `expected browser transcript, got: ${textC}`);
});

// 5. Voice mode transcribeUtterance resolution order + browser fallback acceptance
await check('voice_mode_accepts_device_server_and_browser_fallback', async () => {
  ds.invalidateDeviceProbeCache();
  ds.saveDeviceSpeechSettings({ enabled: false });
  fetchHandler = async () => ({ ok: false, status: 503, json: async () => ({}) });

  const blob = new context.Blob(['x'], { type: 'audio/webm' });
  const browserRes = await vmMod.transcribeUtterance(blob, 'hello from phone browser');
  assert(browserRes === 'hello from phone browser', `voiceMode did not use browser fallback: ${browserRes}`);
});

// 6. TTS resolution order: (a) device -> (b) server -> (c) browser speechSynthesis
await check('tts_resolution_order_device_then_server_then_browser', async () => {
  ds.invalidateDeviceProbeCache();
  ds.saveDeviceSpeechSettings({
    enabled: true,
    url: 'http://localhost:8000/v1',
    ttsModel: 'speaches-ai/Kokoro-82M-v1.0-ONNX',
    ttsVoice: 'af_heart',
  });

  let deviceTtsCalled = false;
  fetchHandler = async (url, opts) => {
    if (url === '/api/tts/stats') {
      return { ok: true, status: 200, json: async () => ({ available: false, ready: false, provider: 'disabled' }) };
    }
    if (url === 'http://localhost:8000/v1/models') {
      return { ok: true, status: 200, json: async () => ({ data: [{ id: 'speaches-ai/Kokoro-82M-v1.0-ONNX' }] }) };
    }
    if (url === 'http://localhost:8000/v1/audio/speech') {
      deviceTtsCalled = true;
      const body = JSON.parse(opts.body);
      assert(body.model === 'speaches-ai/Kokoro-82M-v1.0-ONNX', `unexpected tts model: ${body.model}`);
      assert(body.voice === 'af_heart', `unexpected tts voice: ${body.voice}`);
      assert(body.input === 'Hello world', `unexpected tts input: ${body.input}`);
      return { ok: true, status: 200, blob: async () => new context.Blob(['mp3'], { type: 'audio/mpeg' }) };
    }
    throw new Error('unexpected url: ' + url);
  };

  const mgr = new AITTSManager();
  await mgr.checkAvailability();
  assert(mgr.available === true && mgr._provider === 'device', `expected device provider, got ${mgr._provider}`);
  const audioUrl = await mgr.synthesize('Hello world');
  assert(deviceTtsCalled === true, 'device /audio/speech was not called');
  assert(audioUrl === 'blob:fake-audio-url', `expected blob url, got ${audioUrl}`);

  // When device is unreachable and server is not configured -> falls back to browser speechSynthesis
  ds.invalidateDeviceProbeCache();
  mgr.clearCache();
  spokenUtterances = [];
  fetchHandler = async (url) => {
    if (url === '/api/tts/stats') {
      return { ok: true, status: 200, json: async () => ({ available: false, ready: false, provider: 'disabled' }) };
    }
    if (url === 'http://localhost:8000/v1/models') {
      throw new TypeError('Failed to fetch');
    }
    throw new Error('unexpected url: ' + url);
  };
  await mgr.checkAvailability();
  assert(mgr.available === true && mgr._provider === 'browser', `expected browser fallback provider, got ${mgr._provider}`);
  await mgr.play('Spoken on phone');
  assert(spokenUtterances.includes('Spoken on phone'), 'speechSynthesis.speak was not called for browser fallback');
});

// 7. Settings UI Test button wiring
await check('settings_ui_test_button_probes_and_reports', async () => {
  store.clear();
  ds.invalidateDeviceProbeCache();
  const urlEl = context.document.getElementById('set-deviceSpeechUrl');
  const enEl = context.document.getElementById('set-deviceSpeechEnabled');
  const sttEl = context.document.getElementById('set-deviceSpeechSttModel');
  const ttsEl = context.document.getElementById('set-deviceSpeechTtsModel');
  const voiceEl = context.document.getElementById('set-deviceSpeechTtsVoice');
  const testBtn = context.document.getElementById('set-deviceSpeechTestBtn');
  const msgEl = context.document.getElementById('set-deviceSpeechMsg');

  speechSettingsMod.namespace.initDeviceSpeechSettings();
  assert(urlEl.value === 'http://localhost:8000/v1', 'initial url input not populated');

  fetchHandler = async (url) => {
    if (url === 'http://localhost:8000/v1/models') {
      return { ok: true, status: 200, json: async () => ({ data: [{ id: 'kokoro' }] }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  enEl.checked = true;
  testBtn.dispatchEvent({ type: 'click' });
  await new Promise(r => setTimeout(r, 20));
  assert(/Connected/i.test(msgEl.textContent), `expected Connected in test msg, got: ${msgEl.textContent}`);
});

console.log(JSON.stringify(results));
