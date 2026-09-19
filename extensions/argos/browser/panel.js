const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const stopBtn = document.getElementById("stop-btn");
const contextBar = document.getElementById("context-bar");
const contextTitle = document.getElementById("context-title");
const recaptureBtn = document.getElementById("recapture-btn");
const setupNotice = document.getElementById("setup-notice");
const openOptionsBtn = document.getElementById("open-options-btn");
const optionsBtn = document.getElementById("options-btn");

let port = null;
let capturedPage = null;
let asking = false;
let assistantBubble = null;

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function setAsking(value) {
  asking = value;
  sendBtn.disabled = value;
  stopBtn.hidden = !value;
}

function connectPort() {
  port = chrome.runtime.connect({ name: "argos-panel" });
  port.onMessage.addListener((msg) => {
    if (msg.type === "captured") {
      capturedPage = msg.page;
      contextTitle.textContent = msg.page.title || msg.page.url || "Untitled page";
      contextBar.hidden = false;
      return;
    }
    if (msg.type === "capture-error") {
      contextTitle.textContent = "Could not read this page";
      contextBar.hidden = false;
      return;
    }
    if (msg.type === "delta") {
      if (!assistantBubble) {
        assistantBubble = addMessage("assistant", "");
      }
      assistantBubble.textContent += msg.text;
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return;
    }
    if (msg.type === "event") {
      // Typed SSE events (metrics, message_saved, ...) are not rendered in
      // v1 -- only the streamed answer text is. Kept as a no-op branch so
      // future surfaces (sources, token counts) have a place to hook in.
      return;
    }
    if (msg.type === "done") {
      assistantBubble = null;
      setAsking(false);
      return;
    }
    if (msg.type === "stopped") {
      setAsking(false);
      return;
    }
    if (msg.type === "error") {
      addMessage("error", msg.message || "Something went wrong.");
      assistantBubble = null;
      setAsking(false);
      return;
    }
  });
  port.onDisconnect.addListener(() => {
    port = null;
  });
}

function requestCapture() {
  if (!port) connectPort();
  port.postMessage({ type: "capture" });
}

function sendQuestion() {
  const text = inputEl.value.trim();
  if (!text || asking) return;
  if (!port) connectPort();

  addMessage("user", text);
  inputEl.value = "";
  setAsking(true);
  port.postMessage({ type: "ask", message: text, page: capturedPage });
}

function stopAsking() {
  if (!port) return;
  port.postMessage({ type: "stop" });
}

async function init() {
  const stored = await chrome.storage.local.get(["serverUrl", "token"]);
  if (!stored.serverUrl || !stored.token) {
    setupNotice.hidden = false;
    inputEl.disabled = true;
    sendBtn.disabled = true;
    return;
  }
  connectPort();
  requestCapture();
}

sendBtn.addEventListener("click", sendQuestion);
stopBtn.addEventListener("click", stopAsking);
recaptureBtn.addEventListener("click", requestCapture);
openOptionsBtn.addEventListener("click", () => chrome.runtime.openOptionsPage());
optionsBtn.addEventListener("click", () => chrome.runtime.openOptionsPage());

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendQuestion();
  }
});

inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, 120)}px`;
});

init();
