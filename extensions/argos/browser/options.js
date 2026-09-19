const serverUrlEl = document.getElementById("server-url");
const tokenEl = document.getElementById("token");
const grantBtn = document.getElementById("grant-btn");
const testBtn = document.getElementById("test-btn");
const saveBtn = document.getElementById("save-btn");
const statusEl = document.getElementById("status");
const modelCard = document.getElementById("model-card");
const modelSelect = document.getElementById("model-select");

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

function normalizedServerUrl() {
  return serverUrlEl.value.trim().replace(/\/+$/, "");
}

async function loadStoredSettings() {
  const stored = await chrome.storage.local.get([
    "serverUrl",
    "token",
    "endpointId",
    "model",
  ]);
  serverUrlEl.value = stored.serverUrl || "";
  tokenEl.value = stored.token || "";
  if (stored.serverUrl && stored.token) {
    await loadModels(stored.serverUrl, stored.token, stored.endpointId, stored.model);
  }
}

async function loadModels(serverUrl, token, selectedEndpointId, selectedModel) {
  try {
    const resp = await fetch(`${serverUrl}/api/companion/models`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "omit",
    });
    if (!resp.ok) return;
    const data = await resp.json();
    modelSelect.innerHTML = "";
    for (const ep of data.endpoints || []) {
      for (const modelId of ep.models || []) {
        const opt = document.createElement("option");
        opt.value = JSON.stringify({ endpointId: ep.endpoint_id, model: modelId });
        opt.textContent = `${ep.name} — ${modelId}`;
        if (ep.endpoint_id === selectedEndpointId && modelId === selectedModel) {
          opt.selected = true;
        }
        modelSelect.appendChild(opt);
      }
    }
    modelCard.hidden = modelSelect.options.length === 0;
  } catch (err) {
    // Leave the model card hidden; the test-connection button surfaces
    // network errors more directly.
  }
}

grantBtn.addEventListener("click", async () => {
  const serverUrl = normalizedServerUrl();
  if (!serverUrl) {
    setStatus("Enter a server URL first.", "err");
    return;
  }
  try {
    const origin = new URL(serverUrl).origin + "/*";
    const granted = await chrome.permissions.request({ origins: [origin] });
    setStatus(granted ? "Site access granted." : "Site access was not granted.", granted ? "ok" : "err");
  } catch (err) {
    setStatus(`Invalid URL: ${err.message}`, "err");
  }
});

testBtn.addEventListener("click", async () => {
  const serverUrl = normalizedServerUrl();
  const token = tokenEl.value.trim();
  if (!serverUrl || !token) {
    setStatus("Enter a server URL and token first.", "err");
    return;
  }
  setStatus("Testing...", "");
  try {
    const resp = await fetch(`${serverUrl}/api/argos/hello`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "omit",
    });
    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      setStatus(`Failed (HTTP ${resp.status}): ${body}`, "err");
      return;
    }
    const data = await resp.json();
    setStatus(`Connected as ${data.owner} — Odysseus ${data.version}.`, "ok");
    await loadModels(serverUrl, token);
  } catch (err) {
    setStatus(`Could not reach server: ${err.message}`, "err");
  }
});

saveBtn.addEventListener("click", async () => {
  const serverUrl = normalizedServerUrl();
  const token = tokenEl.value.trim();
  if (!serverUrl || !token) {
    setStatus("Enter a server URL and token first.", "err");
    return;
  }
  await chrome.storage.local.set({ serverUrl, token });
  // A fresh session should be created against whatever model is now
  // configured, not one cached under the previous server/token.
  await chrome.storage.session.clear();
  setStatus("Saved.", "ok");
  await loadModels(serverUrl, token);
});

modelSelect.addEventListener("change", async () => {
  if (!modelSelect.value) return;
  const { endpointId, model } = JSON.parse(modelSelect.value);
  await chrome.storage.local.set({ endpointId, model });
  await chrome.storage.session.clear();
  setStatus("Default model saved.", "ok");
});

loadStoredSettings();
