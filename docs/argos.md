# Argos

Argos is a Chrome/Brave browser extension — Leo-style Q&A about the page
you're on, answered in a side panel, backed by your own Odysseus instance.
It's also the seed of a fuller AI browser companion: the extension drives a
real Odysseus chat session (persisted, visible in the app, streamed like any
other chat), not a side channel.

> Looking for the extension system itself (enabling/disabling, installing
> from a URL)? See [`extensions.md`](extensions.md). This page is about
> Argos specifically, once it's enabled.

## The short version

- **Enable it first.** Argos ships `enabled_by_default: false` — it mints
  credentials, so an admin opts in from Settings → Extensions, then
  restarts (extensions have no hot reload).
- Open the in-app **Argos** page (sidebar, or the `/argos` deep link),
  download the browser extension zip, and load it unpacked in Chrome or
  Brave.
- Mint a pairing token on that same page and paste it — along with the
  server's URL — into the extension's Options page.
- Click the toolbar icon on any page to open the side panel and ask about
  it. The extension captures the page's text (or your current selection, if
  you have one) and sends it along with your question.

## Installing the browser extension

1. In Odysseus: Settings → Extensions → enable **Argos** → restart.
2. Open `/argos` and click **Download argos-browser-extension.zip**, then
   extract it.
3. In Chrome or Brave, open `chrome://extensions` (or
   `brave://extensions`), turn on **Developer mode**, click **Load
   unpacked**, and pick the extracted folder.
4. Click the extension's icon once so Chrome grants a toolbar icon; it opens
   the side panel (or a popup, on older Brave/Chromium builds without side
   panel support).

## Pairing

On the same `/argos` page (admin only): **Mint pairing token**. The token is
shown once — copy it. In the extension's Options page, paste:
- **Server URL** — this Odysseus instance's URL (shown right below the mint
  button), e.g. `http://127.0.0.1:24950` or your Tailscale/LAN address.
- **Pairing token** — what you just minted.

Click **Test connection** to confirm both are correct, then pick a default
model from the list (pulled live from your configured model endpoints) and
**Save**.

**Revoke access any time** from Settings → Integrations → API Tokens — the
same table every other token (including phone companion pairings) lives in.
Argos tokens are named "browser extension" and scoped `chat, argos:ask`.

### Granting site access

The extension has zero host permissions by default — it can't reach your
server until you explicitly grant it. Click **Grant site access** on the
Options page after entering your server URL; this is a one-time browser
permission prompt, not something Odysseus tracks.

## What gets sent, and what doesn't

When you ask a question, the extension captures the active tab's visible
text (or just your selection, if you made one) via `chrome.scripting`, plus
the page's URL and title — no persistent content script, no access to tabs
you haven't explicitly acted on. That gets posted to your Odysseus instance
and wrapped as **untrusted context** before it ever reaches the model —
the same mechanism used for fetched web pages, RAG results, and email
bodies (`src/prompt_security.py`). A page trying to smuggle instructions
("ignore previous instructions and...") is treated as data to read, not
commands to follow.

**Chat turns from Argos run with agent tools, plan mode, research, and
workspace access all turned off.** A page you're reading is fully
attacker-controlled by definition, so v1 deliberately answers questions
about it without giving the model anything it could be tricked into doing.

The bearer token lives only in the extension's `chrome.storage.local`,
read only by its service worker — never in the side panel's DOM, never
injected into a page, never visible to the page itself.

## How it fits together

Each tab gets its own Odysseus session (created once, cached for the
tab), so a conversation about one page doesn't bleed into another. Every
question:
1. Sends the captured page as context (deduped — asking a second question
   about an unchanged page doesn't re-send it).
2. Drives the same `/api/chat_stream` endpoint the desktop app uses,
   streamed token-by-token into the panel.
3. Persists like any other chat — open the session in the Odysseus app
   itself to see the full transcript, including what page context was sent.

## Troubleshooting

**"Not paired yet"** in the panel — open the extension's Options page and
fill in the server URL + token, then Test connection.

**CORS error / request fails from the extension but curl works** — some
Brave/Chromium versions don't exempt extension service-worker fetches from
CORS the way current Chrome does. Add the extension's origin to
`ALLOWED_ORIGINS` on the server: copy the extension ID from
`chrome://extensions`, then set
`ALLOWED_ORIGINS=http://localhost,chrome-extension://<id>` and restart.

**"Page changed — click the icon to re-capture"** — the page-read
permission (`activeTab`) is per-visit and expires on navigation; click the
toolbar icon again, or turn on "Always allow on this site" in the panel if
you want it to recapture automatically.

**No side panel, just a popup** — expected on Brave/Chromium builds without
`chrome.sidePanel` support; the extension falls back to a classic popup
automatically.

**"No model selected for this chat"** — pick a default model on the
extension's Options page; it needs a real model endpoint the same way any
Odysseus session does.
