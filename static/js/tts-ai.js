// static/js/tts-ai.js
// AI Text-to-Speech Module — supports device server (Speaches), server TTS, and browser Web Speech API

import { getSettings } from './appConfig.js';
import {
    getDeviceSpeechSettings,
    probeDeviceServer,
    synthesizeOnDevice,
    isServerTtsConfigured,
    hasBrowserTTS,
} from './deviceSpeech.js';

class AITTSManager {
    constructor() {
        this.currentAudio = null;
        this.isPlaying = false;
        this.available = false;
        this.useBrowserTTS = false;
        this.browserVoice = '';
        this.playbackSpeed = 1;
        this._provider = 'disabled';
        this._serverStats = null;
        this._serverConfigured = false;
        this.autoPlay = false;
        this.cache = new Map(); // Client-side audio cache

        // Queue for sequential auto-play
        this._queue = [];       // Array of { text, button, resetFn }
        this._processing = false;

        // Streaming sentence-by-sentence TTS state
        this._streamSentencesSent = 0;  // chars of plain text already queued
        this._streamActive = false;
        this._streamButton = null;
        this._streamResetFn = null;
        this._streamDebounceTimer = null;

        // Tunables live voice mode (voiceMode.js) adjusts while it runs; the
        // defaults keep normal read-aloud exactly as before.
        this.minSentenceChars = 15;   // shorter streamed sentences are skipped
        this.earlyFlush = false;      // speak a long first clause before its full stop
        this.prefetch = false;        // synthesize the next queued item while one plays
        this.modelEndpointId = null;  // chat's endpoint, for speech routing on the server
        this._inflight = new Map();   // cacheKey -> pending synthesis promise
        this._listeners = {};         // 'playbackstart' | 'queuedrained' | 'stopped'

        // Check if TTS service is available
        this.checkAvailability();
    }

    on(event, fn) {
        (this._listeners[event] = this._listeners[event] || []).push(fn);
        return () => {
            this._listeners[event] = (this._listeners[event] || []).filter(f => f !== fn);
        };
    }

    _emit(event) {
        for (const fn of (this._listeners[event] || []).slice()) {
            try { fn(); } catch (e) { console.warn('TTS listener error:', e); }
        }
    }

    async checkAvailability() {
        try {
            const devCfg = getDeviceSpeechSettings();
            // Check user setting first — if TTS is disabled in server settings and
            // device speech is not enabled, don't show read-aloud buttons.
            let serverTtsEnabled = true;
            try {
                const settings = await getSettings();
                if (settings.tts_enabled === false) {
                    serverTtsEnabled = false;
                }
            } catch {}

            let stats = null;
            try {
                const response = await fetch('/api/tts/stats');
                if (response.ok) {
                    stats = await response.json();
                }
            } catch (e) {
                console.warn('Failed to fetch /api/tts/stats:', e);
            }
            this._serverStats = stats;
            this.playbackSpeed = (stats && stats.speed) || 1;
            this.browserVoice = (stats && stats.voice) || '';
            this._serverConfigured = serverTtsEnabled && isServerTtsConfigured(stats);

            // (a) Device server if enabled and /v1/models responds within ~1 s
            if (devCfg.enabled) {
                const probe = await probeDeviceServer();
                if (probe.ok) {
                    this.available = true;
                    this.useBrowserTTS = false;
                    this._provider = 'device';
                    return;
                }
            }

            // (b) Odysseus server routes if configured
            if (this._serverConfigured) {
                this.available = true;
                this.useBrowserTTS = false;
                this._provider = stats.provider || 'local';
                return;
            }

            // (c) Browser fallback: speechSynthesis (if server provider is 'browser',
            // device speech is enabled and fell through, or server has no speech configured
            // and tts_enabled was not explicitly turned off by user).
            if (serverTtsEnabled || devCfg.enabled) {
                if (hasBrowserTTS()) {
                    this.available = true;
                    this.useBrowserTTS = true;
                    this._provider = 'browser';
                    return;
                }
            }

            this.available = false;
            this.useBrowserTTS = false;
            this._provider = 'disabled';
        } catch (error) {
            console.error('Failed to check TTS availability:', error);
            if (hasBrowserTTS()) {
                this.available = true;
                this.useBrowserTTS = true;
                this._provider = 'browser';
            } else {
                this.available = false;
            }
        }
    }

    extractPlainText(content) {
        // Strip <think>/<thinking> blocks (model reasoning). The opening tag may
        // carry attributes (chat.js stamps <think time="12">), and mid-stream
        // the block is still open, so also drop an unclosed block to the end
        // and anything before a stray closing tag whose opener was never sent.
        let cleaned = content
            .replace(/<think(?:ing)?(?:\s[^>]*)?>[\s\S]*?<\/think(?:ing)?>/gi, '')
            .replace(/<think(?:ing)?(?:\s[^>]*)?>[\s\S]*$/i, '')
            .replace(/^[\s\S]*<\/think(?:ing)?>/i, '');

        // Create a temporary div to parse HTML/markdown
        const temp = document.createElement('div');
        temp.innerHTML = cleaned;

        // Remove code blocks
        temp.querySelectorAll('pre, code').forEach(el => el.remove());

        // Get text content
        let text = temp.textContent || temp.innerText || '';

        // Clean up markdown syntax
        text = text
            .replace(/#{1,6}\s/g, '') // Remove headers
            .replace(/\*\*(.+?)\*\*/g, '$1') // Remove bold
            .replace(/\*(.+?)\*/g, '$1') // Remove italic
            .replace(/\[(.+?)\]\(.+?\)/g, '$1') // Remove links
            .replace(/`(.+?)`/g, '$1') // Remove inline code
            .replace(/\n{3,}/g, '\n\n') // Normalize line breaks
            .trim();

        return text;
    }

    getCacheKey(text) {
        const devCfg = getDeviceSpeechSettings();
        const salt = devCfg.enabled ? `${devCfg.url}|${devCfg.ttsModel}|${devCfg.ttsVoice}|` : '';
        const input = salt + text;
        let hash = 0;
        for (let i = 0; i < input.length; i++) {
            const char = input.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
        }
        return hash.toString(36);
    }

    async synthesize(text, onProgress = null) {
        if (!this.available && !getDeviceSpeechSettings().enabled && !hasBrowserTTS()) {
            throw new Error('AI TTS service not available');
        }

        const plainText = this.extractPlainText(text);

        if (!plainText) {
            throw new Error('No text to synthesize');
        }

        const devCfg = getDeviceSpeechSettings();

        // When neither device speech nor server TTS is active, use browser TTS directly
        if (!devCfg.enabled && this.useBrowserTTS && !this._serverConfigured) {
            return '__browser_tts__';
        }

        const cacheKey = this.getCacheKey(plainText);

        // Check cache first
        if (this.cache.has(cacheKey)) {
            return this.cache.get(cacheKey);
        }
        // A prefetch for the same text may already be on the wire.
        if (this._inflight.has(cacheKey)) {
            return this._inflight.get(cacheKey);
        }
        const pending = this._synthesizeRemote(plainText, cacheKey, onProgress);
        this._inflight.set(cacheKey, pending);
        try {
            return await pending;
        } finally {
            this._inflight.delete(cacheKey);
        }
    }

    async _synthesizeRemote(plainText, cacheKey, onProgress) {
        if (onProgress) onProgress('synthesizing');
        const devCfg = getDeviceSpeechSettings();
        let lastError = null;

        // (a) Device server if enabled and /v1/models responds within ~1 s
        if (devCfg.enabled) {
            const probe = await probeDeviceServer();
            if (probe.ok) {
                try {
                    const audioBlob = await synthesizeOnDevice(plainText, {
                        speed: this.playbackSpeed,
                    });
                    const audioUrl = URL.createObjectURL(audioBlob);
                    this.cache.set(cacheKey, audioUrl);
                    this.useBrowserTTS = false;
                    this._provider = 'device';
                    if (onProgress) onProgress('complete');
                    return audioUrl;
                } catch (err) {
                    lastError = err;
                    console.warn('Device TTS failed, falling through:', err.message);
                }
            }
        }

        // (b) Odysseus server routes if configured
        if (this._serverConfigured || (this._provider !== 'browser' && this._provider !== 'disabled' && this._provider !== 'device')) {
            try {
                const body = { text: plainText, format: 'audio' };
                if (this.modelEndpointId) body.model_endpoint_id = this.modelEndpointId;
                const response = await fetch('/api/tts/synthesize', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(body)
                });

                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.detail?.message || 'Synthesis failed');
                }

                const audioBlob = await response.blob();
                const audioUrl = URL.createObjectURL(audioBlob);
                this.cache.set(cacheKey, audioUrl);
                this.useBrowserTTS = false;
                if (onProgress) onProgress('complete');
                return audioUrl;
            } catch (err) {
                lastError = err;
                console.warn('Server TTS failed, falling through to browser:', err.message);
            }
        }

        // (c) Browser fallback: speechSynthesis
        if (hasBrowserTTS()) {
            if (onProgress) onProgress('complete');
            return '__browser_tts__';
        }

        if (onProgress) onProgress('error');
        throw lastError || new Error('Synthesis failed');
    }

    _findBrowserVoice() {
        if (!this.browserVoice) return null;
        const voices = window.speechSynthesis.getVoices();
        const target = this.browserVoice.toLowerCase();
        // Try exact match first, then partial
        return voices.find(v => v.name.toLowerCase() === target) ||
               voices.find(v => v.name.toLowerCase().includes(target)) ||
               null;
    }

    async play(text) {
        // Stop current audio if playing
        this.stop();

        const plainText = this.extractPlainText(text);
        if (!plainText) return;

        try {
            const audioUrl = await this.synthesize(text);
            if (audioUrl === '__browser_tts__') {
                return await this._playBrowser(plainText);
            }

            this.currentAudio = new Audio(audioUrl);
            await this.currentAudio.play();
            this.isPlaying = true;
            // Note: onended should be set by the caller (addAITTSButton)
            // to reset button state when audio finishes

        } catch (error) {
            console.error('Failed to play audio:', error);
            throw error;
        }
    }

    _playBrowser(plainText) {
        return new Promise((resolve, reject) => {
            const utterance = new SpeechSynthesisUtterance(plainText);
            const voice = this._findBrowserVoice();
            if (voice) utterance.voice = voice;
            utterance.rate = this.playbackSpeed;

            utterance.onstart = () => this._emit('playbackstart');
            utterance.onend = () => {
                this.isPlaying = false;
                resolve();
            };
            utterance.onerror = (e) => {
                this.isPlaying = false;
                reject(new Error('Browser TTS error: ' + e.error));
            };

            window.speechSynthesis.speak(utterance);
            this.isPlaying = true;
        });
    }

    stop() {
        // Cancel streaming TTS
        this._streamActive = false;
        if (this._streamDebounceTimer) {
            clearTimeout(this._streamDebounceTimer);
            this._streamDebounceTimer = null;
        }
        this._streamSentencesSent = 0;

        // Clear the entire queue and reset all queued buttons
        for (const item of this._queue) {
            if (item.resetFn) item.resetFn();
        }
        this._queue = [];
        this._processing = false;

        if (hasBrowserTTS()) {
            try { window.speechSynthesis.cancel(); } catch (_) {}
            this.isPlaying = false;
        }
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            this.currentAudio = null;
            this.isPlaying = false;
        }
        this._emit('stopped');
    }

    /**
     * Enqueue a message for auto-play. Plays sequentially — each message
     * finishes before the next starts. Stopping any message clears the queue.
     */
    enqueue(text, button, resetFn) {
        this._queue.push({ text, button, resetFn });
        if (!this._processing) {
            this._processQueue();
        }
    }

    async _processQueue() {
        if (this._processing) return;
        this._processing = true;

        while (this._queue.length > 0) {
            const item = this._queue[0];
            // Synthesize the next item while this one plays, so sentences
            // follow each other without a gap (voice mode turns this on).
            if (this.prefetch && (!this.useBrowserTTS || getDeviceSpeechSettings().enabled) && this._queue[1]) {
                this.synthesize(this._queue[1].text).catch(() => {});
            }
            try {
                await this._playQueueItem(item);
            } catch (err) {
                console.error('TTS queue item error:', err);
            }
            if (this._queue.length > 0 && this._queue[0] === item) {
                this._queue.shift();
            }
            if (!this._processing) return;
        }

        this._processing = false;
        this._emit('queuedrained');
    }

    async _playQueueItem(item) {
        const { text, button, resetFn } = item;
        const ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';
        var ICON_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';

        button.innerHTML = ICON_LOADING;
        button.classList.add('loading');
        button.style.color = '#ccc';
        button.title = 'Loading...';

        try {
            if (!this._processing) return;

            const audioUrl = await this.synthesize(text);

            if (!this._processing) return;

            button.innerHTML = ICON_STOP;
            button.classList.remove('loading');
            button.classList.add('playing');
            button.title = 'Stop';

            if (audioUrl === '__browser_tts__') {
                const plainText = this.extractPlainText(text);
                await this._playBrowser(plainText);
            } else {
                if (this.currentAudio) {
                    this.currentAudio.pause();
                    this.currentAudio = null;
                }

                await new Promise((resolve, reject) => {
                    const audio = new Audio(audioUrl);
                    if (this._provider === 'local' && this.playbackSpeed !== 1) {
                        audio.playbackRate = this.playbackSpeed;
                    }
                    this.currentAudio = audio;
                    audio.onended = () => {
                        this.isPlaying = false;
                        if (this.currentAudio === audio) this.currentAudio = null;
                        resolve();
                    };
                    audio.onerror = (e) => {
                        this.isPlaying = false;
                        if (this.currentAudio === audio) this.currentAudio = null;
                        reject(new Error('Audio playback error'));
                    };
                    audio.onpause = () => {
                        if (this.currentAudio !== audio) {
                            resolve();
                        }
                    };
                    audio.play().then(() => {
                        this.isPlaying = true;
                        this._emit('playbackstart');
                    }).catch(reject);
                });
            }
        } finally {
            if (resetFn) resetFn();
        }
    }

    // ── Streaming TTS (sentence-by-sentence) ──

    streamingStart() {
        this._streamSentencesSent = 0;
        this._streamActive = true;
        this._streamButton = null;
        this._streamResetFn = null;
    }

    streamingUpdate(accumulatedText) {
        if (!this._streamActive || !this.available || !this.autoPlay) return;
        if (this._streamDebounceTimer) return;
        this._streamDebounceTimer = setTimeout(() => {
            this._streamDebounceTimer = null;
            this._processStreamingSentences(accumulatedText);
        }, 150);
    }

    _processStreamingSentences(accumulatedText) {
        if (!this._streamActive) return;

        var text = accumulatedText
            .replace(/```[\s\S]*?```/g, '')
            .replace(/```[\s\S]*$/g, '');

        var plainText = this.extractPlainText(text);
        if (!plainText || plainText.length <= this._streamSentencesSent) return;

        var newRegion = plainText.substring(this._streamSentencesSent);

        var sentences = [];
        var current = '';
        for (var i = 0; i < newRegion.length; i++) {
            current += newRegion[i];
            var ch = newRegion[i];
            var next = newRegion[i + 1];
            if ((ch === '.' || ch === '!' || ch === '?') && next && /\s/.test(next)) {
                var lastWord = current.trim().split(/\s/).pop() || '';
                if (/^\d+\.$/.test(lastWord)) continue;
                if (/^[A-Z][a-z]?\.$/.test(lastWord)) continue;
                sentences.push(current.trim());
                current = '';
            }
        }

        // Voice mode: speak a long opening clause at its comma instead of
        // waiting for the full stop, to cut time-to-first-audio.
        if (sentences.length === 0 && this.earlyFlush && this._streamSentencesSent === 0
            && current.length >= 60) {
            var cut = Math.max(current.lastIndexOf(', '), current.lastIndexOf('; '));
            if (cut >= 40) sentences.push(current.substring(0, cut + 1).trim());
        }

        if (sentences.length === 0) return;

        var advancedChars = 0;
        for (var j = 0; j < sentences.length; j++) {
            var sentence = sentences[j];
            if (sentence.length < this.minSentenceChars) {
                advancedChars += sentence.length + 1;
                continue;
            }
            var btn = this._streamButton || this._createPlaceholderButton();
            var resetFn = this._streamResetFn || function() {};
            this.enqueue(sentence, btn, resetFn);
            advancedChars += sentence.length + 1;
        }

        this._streamSentencesSent += advancedChars;
    }

    _createPlaceholderButton() {
        var btn = document.createElement('button');
        btn.style.display = 'none';
        btn.className = 'ai-tts-button streaming-placeholder';
        return btn;
    }

    streamingAttachButton(button, resetFn) {
        this._streamButton = button;
        this._streamResetFn = resetFn;
        for (var i = 0; i < this._queue.length; i++) {
            if (this._queue[i].button && this._queue[i].button.classList.contains('streaming-placeholder')) {
                this._queue[i].button = button;
                this._queue[i].resetFn = resetFn;
            }
        }
    }

    streamingEnd(finalText) {
        if (!this._streamActive) return;
        this._streamActive = false;
        if (this._streamDebounceTimer) {
            clearTimeout(this._streamDebounceTimer);
            this._streamDebounceTimer = null;
        }

        var text = finalText
            .replace(/```[\s\S]*?```/g, '')
            .replace(/```[\s\S]*$/g, '');

        var plainText = this.extractPlainText(text);
        if (!plainText) return;

        var remaining = plainText.substring(this._streamSentencesSent).trim();
        if (remaining.length >= this.minSentenceChars) {
            var btn = this._streamButton || this._createPlaceholderButton();
            var resetFn = this._streamResetFn || function() {};
            this.enqueue(remaining, btn, resetFn);
        }
        this._streamSentencesSent = 0;
    }

    clearCache() {
        for (const url of this.cache.values()) {
            URL.revokeObjectURL(url);
        }
        this.cache.clear();
    }
}

// Create global AI TTS manager instance
window.aiTTSManager = new AITTSManager();

// Function to add AI TTS button to a message element's action bar
export function addAITTSButton(messageElement, text) {
    if (!window.aiTTSManager.available || window.aiTTSManager._provider === 'disabled') {
        return;
    }

    if (messageElement.querySelector('.ai-tts-button')) {
        return;
    }

    // Find the msg-actions container in the footer
    const actions = messageElement.querySelector('.msg-actions');
    if (!actions) return;

    var ICON_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6 3 20 12 6 21 6 3"/></svg>';
    var ICON_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
    var ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';

    const playButton = document.createElement('button');
    playButton.className = 'ai-tts-button';
    playButton.type = 'button';
    playButton.title = 'Read aloud';
    playButton.innerHTML = ICON_PLAY;
    playButton.style.cssText = 'background:none;border:none;color:#6b7280;cursor:pointer;padding:2px 6px;border-radius:4px;transition:color .15s;line-height:1;display:inline-flex;align-items:center;';

    playButton.addEventListener('mouseenter', () => { playButton.style.color = '#ccc'; });
    playButton.addEventListener('mouseleave', () => {
        if (!playButton.classList.contains('playing') && !playButton.classList.contains('loading')) playButton.style.color = '#6b7280';
    });

    function resetButton() {
        playButton.innerHTML = ICON_PLAY;
        playButton.classList.remove('playing', 'loading');
        playButton.style.color = '#6b7280';
        playButton.title = 'Read aloud';
    }

    playButton.addEventListener('click', async (e) => {
        e.stopPropagation();
        const mgr = window.aiTTSManager;

        if (mgr.isPlaying || mgr._processing) {
            mgr.stop();
            resetButton();
            return;
        }

        mgr.enqueue(text, playButton, resetButton);
    });

    actions.appendChild(playButton);
}

// Stop audio when navigating away
window.addEventListener('beforeunload', () => {
    if (window.aiTTSManager) {
        window.aiTTSManager.stop();
    }
});

export { AITTSManager };

const ttsModule = { AITTSManager, addAITTSButton };
export default ttsModule;
