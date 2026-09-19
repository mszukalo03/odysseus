// Argos service worker — all network I/O to the Odysseus server lives here,
// never in the panel's DOM context. Extension service workers with a
// granted host permission are exempt from CORS, and keeping the bearer
// token out of the panel means a compromised page can never read it (the
// page can't reach into the service worker's storage).
import { extractPage } from "./capture.js";

chrome.runtime.onInstalled.addListener(async () => {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    try {
      await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
      return;
    } catch (err) {
      // fall through to the popup fallback below
    }
  }
  // Older Chromium / Brave builds without chrome.sidePanel: fall back to a
  // classic popup so the extension still works.
  try {
    await chrome.action.setPopup({ popup: "panel.html" });
  } catch (err) {
    // nothing more we can do
  }
});

async function getSettings() {
  const stored = await chrome.storage.local.get([
    "serverUrl",
    "token",
    "endpointId",
    "model",
  ]);
  return {
    serverUrl: (stored.serverUrl || "").replace(/\/+$/, ""),
    token: stored.token || "",
    endpointId: stored.endpointId || "",
    model: stored.model || "",
  };
}

function apiUrl(serverUrl, path) {
  return `${serverUrl}${path}`;
}

async function apiFetch(serverUrl, token, path, options = {}) {
  const resp = await fetch(apiUrl(serverUrl, path), {
    ...options,
    credentials: "omit",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  return resp;
}

async function ensureSession(settings, tabId) {
  const key = `argos_session_${tabId}`;
  const stored = await chrome.storage.session.get([key]);
  const cached = stored[key];
  if (cached && cached.endpointId === settings.endpointId && cached.model === settings.model) {
    return cached.sessionId;
  }

  const form = new FormData();
  form.set("name", "Argos");
  form.set("endpoint_id", settings.endpointId);
  form.set("model", settings.model);

  const resp = await apiFetch(settings.serverUrl, settings.token, "/api/session", {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    throw new Error(`Could not start a session (HTTP ${resp.status})`);
  }
  const data = await resp.json();
  await chrome.storage.session.set({
    [key]: { sessionId: data.id, endpointId: settings.endpointId, model: settings.model },
  });
  return data.id;
}

async function capturePage(tabId) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPage,
  });
  return result;
}

async function sendPageContext(settings, sessionId, page) {
  const form = new FormData();
  form.set("session", sessionId);
  form.set("url", page.url || "");
  form.set("title", page.title || "");
  form.set("text", page.text || "");
  form.set("selection", page.selection || "");
  const resp = await apiFetch(settings.serverUrl, settings.token, "/api/argos/context", {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`Could not send page context (HTTP ${resp.status}) ${detail}`);
  }
  return resp.json();
}

async function* readSseFrames(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          yield line.slice(6);
        }
      }
    }
  }
}

async function askQuestion(settings, sessionId, message, port) {
  const form = new FormData();
  form.set("message", message);
  form.set("session", sessionId);
  form.set("mode", "chat");
  form.set("no_tools", "true");

  const resp = await apiFetch(settings.serverUrl, settings.token, "/api/chat_stream", {
    method: "POST",
    body: form,
  });
  if (!resp.ok || !resp.body) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`Chat request failed (HTTP ${resp.status}) ${detail}`);
  }

  const runId = resp.headers.get("X-Odysseus-Run-Id");
  if (runId) {
    await chrome.storage.session.set({ [`argos_run_${sessionId}`]: runId });
  }

  for await (const raw of readSseFrames(resp)) {
    if (raw === "[DONE]") {
      port.postMessage({ type: "done" });
      return;
    }
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch (err) {
      continue;
    }
    if (typeof payload.delta === "string") {
      port.postMessage({ type: "delta", text: payload.delta });
    } else if (payload.type) {
      port.postMessage({ type: "event", name: payload.type, data: payload.data });
    }
  }
  port.postMessage({ type: "done" });
}

async function stopRun(settings, sessionId) {
  const stored = await chrome.storage.session.get([`argos_run_${sessionId}`]);
  const runId = stored[`argos_run_${sessionId}`];
  const headers = runId ? { "X-Odysseus-Run-Id": runId } : {};
  await apiFetch(settings.serverUrl, settings.token, `/api/chat/stop/${sessionId}`, {
    method: "POST",
    headers,
  });
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "argos-panel") return;

  port.onMessage.addListener(async (msg) => {
    try {
      const settings = await getSettings();
      if (!settings.serverUrl || !settings.token) {
        port.postMessage({ type: "error", message: "Not paired yet. Open Options to set it up." });
        return;
      }

      if (msg.type === "capture") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || !tab.id) {
          port.postMessage({ type: "capture-error", message: "No active tab." });
          return;
        }
        const page = await capturePage(tab.id);
        port.postMessage({ type: "captured", page, tabId: tab.id });
        return;
      }

      if (msg.type === "ask") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const sessionId = await ensureSession(settings, tab ? tab.id : 0);
        if (msg.page) {
          await sendPageContext(settings, sessionId, msg.page);
        }
        await askQuestion(settings, sessionId, msg.message, port);
        return;
      }

      if (msg.type === "stop") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const sessionId = await ensureSession(settings, tab ? tab.id : 0);
        await stopRun(settings, sessionId);
        port.postMessage({ type: "stopped" });
        return;
      }
    } catch (err) {
      port.postMessage({ type: "error", message: String((err && err.message) || err) });
    }
  });
});
