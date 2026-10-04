// Thinking level control in the chat composer.
//
// A small toolbar button opens a menu: Auto / Off / Low / Medium / High.
// The choice persists per browser (Storage toggle state) and chat.js sends it
// as the `thinking` form field. Auto sends nothing, so the persona's default
// (or the model's own default) applies. The server maps the level onto each
// provider's own parameter (src/reasoning_control.py); providers it doesn't
// know are left untouched.

import Storage from './storage.js';

const LEVELS = [
  { id: 'auto', label: 'Auto', hint: 'Persona or model default' },
  { id: 'off', label: 'Off', hint: 'Answer directly, no reasoning' },
  { id: 'low', label: 'Low', hint: 'Brief reasoning' },
  { id: 'medium', label: 'Medium', hint: 'Balanced' },
  { id: 'high', label: 'High', hint: 'Think it through' },
];
const TOGGLE_KEY = 'thinkingLevel';

let _menu = null;

function _valid(level) {
  return LEVELS.some(l => l.id === level) ? level : 'auto';
}

export function getLevel() {
  return _valid(Storage.getToggle(TOGGLE_KEY, 'auto'));
}

export function setLevel(level) {
  Storage.setToggle(TOGGLE_KEY, _valid(level));
  _render();
}

// Value for the request, or '' for auto (field omitted).
export function requestValue() {
  const level = getLevel();
  return level === 'auto' ? '' : level;
}

function _render() {
  const btn = document.getElementById('thinking-level-btn');
  if (!btn) return;
  const level = getLevel();
  const entry = LEVELS.find(l => l.id === level);
  const label = btn.querySelector('.thinking-level-label');
  if (label) label.textContent = level === 'auto' ? '' : entry.label;
  btn.classList.toggle('active', level !== 'auto');
  btn.setAttribute('aria-pressed', level !== 'auto' ? 'true' : 'false');
  btn.title = `Thinking: ${entry.label}` + (level === 'auto' ? ' (persona or model default)' : '');
  if (_menu) {
    _menu.querySelectorAll('[data-level]').forEach(item => {
      const on = item.dataset.level === level;
      item.classList.toggle('active', on);
      item.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  }
}

function _closeMenu() {
  if (_menu) _menu.hidden = true;
  const btn = document.getElementById('thinking-level-btn');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function _openMenu(btn) {
  if (!_menu) {
    _menu = document.createElement('div');
    _menu.className = 'thinking-level-menu';
    _menu.setAttribute('role', 'menu');
    _menu.hidden = true;
    LEVELS.forEach(l => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'thinking-level-item';
      item.dataset.level = l.id;
      item.setAttribute('role', 'menuitemradio');
      const name = document.createElement('span');
      name.className = 'thinking-level-name';
      name.textContent = l.label;
      const hint = document.createElement('span');
      hint.className = 'thinking-level-hint';
      hint.textContent = l.hint;
      item.append(name, hint);
      item.addEventListener('click', () => {
        setLevel(l.id);
        _closeMenu();
        btn.focus();
      });
      _menu.appendChild(item);
    });
    document.body.appendChild(_menu);
    document.addEventListener('mousedown', (e) => {
      if (_menu.hidden) return;
      if (_menu.contains(e.target) || btn.contains(e.target)) return;
      _closeMenu();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !_menu.hidden) {
        _closeMenu();
        btn.focus();
      }
    });
  }
  _render();
  const rect = btn.getBoundingClientRect();
  _menu.hidden = false;
  const menuRect = _menu.getBoundingClientRect();
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuRect.width - 8));
  _menu.style.left = `${left}px`;
  _menu.style.top = `${Math.max(8, rect.top - menuRect.height - 6)}px`;
  btn.setAttribute('aria-expanded', 'true');
}

function init() {
  const btn = document.getElementById('thinking-level-btn');
  if (!btn || btn.dataset.thinkingInit) return;
  btn.dataset.thinkingInit = '1';
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    if (_menu && !_menu.hidden) _closeMenu();
    else _openMenu(btn);
  });
  _render();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

const thinkingControl = { getLevel, setLevel, requestValue, init };
window.thinkingControl = thinkingControl;
export default thinkingControl;
