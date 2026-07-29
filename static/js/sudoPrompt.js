// static/js/sudoPrompt.js
//
// Password prompt for agent shell commands that need root.
//
// The agent's bash subprocess has no TTY, so `sudo` can't ask for a password
// itself. When it needs one the backend blocks the command and pushes a
// `sudo_prompt` event down the chat SSE stream; this module renders the modal
// and posts the password back, which unblocks the waiting command.
//
// The password is sent once and never stored client-side — no localStorage,
// no module-level retention beyond the lifetime of the input element.

const API_BASE = window.location.origin;

let _activeRequestId = null;

function _esc(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

function _close(modal) {
  if (!modal) return;
  modal.remove();
  document.removeEventListener('keydown', modal._escHandler, true);
  _activeRequestId = null;
}

async function _post(path, body) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch (_) {
    return false;
  }
}

/**
 * Show the password prompt for a blocked sudo command.
 * @param {string} requestId - correlates with the waiting backend request
 * @param {string} command - the command being authorized (shown for review)
 */
export function showSudoPrompt(requestId, command) {
  if (!requestId || _activeRequestId === requestId) return;
  // A newer prompt supersedes any stale one still on screen.
  document.querySelectorAll('.sudo-prompt-modal').forEach(m => _close(m));
  _activeRequestId = requestId;

  const modal = document.createElement('div');
  modal.className = 'modal sudo-prompt-modal';
  modal.style.display = 'block';
  modal.innerHTML = `
    <div class="modal-content sudo-prompt-content" role="dialog" aria-modal="true" aria-label="Administrator password required">
      <div class="modal-header">
        <h4>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;">
            <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
          Administrator password
        </h4>
      </div>
      <div class="sudo-prompt-body">
        <p class="sudo-prompt-intro">The agent needs root to run this command:</p>
        <pre class="sudo-prompt-command">${_esc(command)}</pre>
        <label class="sudo-prompt-label" for="sudo-prompt-input">Password for this machine</label>
        <input type="password" id="sudo-prompt-input" class="sudo-prompt-input" autocomplete="current-password" spellcheck="false" />
        <label class="sudo-prompt-remember">
          <input type="checkbox" id="sudo-prompt-remember" checked />
          <span>Remember for 15 minutes</span>
        </label>
        <p class="sudo-prompt-note">Kept in memory only — never written to disk, never shown to the model.</p>
        <div class="sudo-prompt-actions">
          <button type="button" class="memory-toolbar-btn" id="sudo-prompt-cancel">Cancel</button>
          <button type="button" class="memory-toolbar-btn sudo-prompt-submit" id="sudo-prompt-submit">Authorize</button>
        </div>
      </div>
    </div>
  `;

  const input = modal.querySelector('#sudo-prompt-input');
  const remember = modal.querySelector('#sudo-prompt-remember');
  const submitBtn = modal.querySelector('#sudo-prompt-submit');

  const cancel = async () => {
    const id = requestId;
    _close(modal);
    await _post('/api/agent/sudo/cancel', { request_id: id });
  };

  const submit = async () => {
    const password = input.value;
    if (!password) {
      input.focus();
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = 'Authorizing…';
    const ok = await _post('/api/agent/sudo/password', {
      request_id: requestId,
      password,
      remember: !!remember.checked,
    });
    // Drop the value regardless of outcome.
    input.value = '';
    if (ok) {
      _close(modal);
    } else {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Authorize';
      const note = modal.querySelector('.sudo-prompt-note');
      if (note) {
        note.textContent = 'That prompt is no longer waiting — it may have timed out.';
        note.classList.add('sudo-prompt-error');
      }
    }
  };

  submitBtn.addEventListener('click', submit);
  modal.querySelector('#sudo-prompt-cancel').addEventListener('click', cancel);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); submit(); }
  });
  modal._escHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); }
  };
  document.addEventListener('keydown', modal._escHandler, true);

  document.body.appendChild(modal);
  setTimeout(() => input.focus(), 0);
}

/**
 * Re-attach to a prompt still waiting server-side (e.g. after a page refresh
 * mid-command). Safe to call on load; no-ops when nothing is pending.
 */
export async function resumePendingSudoPrompt() {
  try {
    const res = await fetch(`${API_BASE}/api/agent/sudo/status`, { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.pending && data.pending.request_id) {
      showSudoPrompt(data.pending.request_id, data.pending.command || '');
    }
  } catch (_) {}
}
