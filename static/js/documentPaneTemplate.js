// static/js/documentPaneTemplate.js
//
// The document editor pane's static HTML shell — header toolbar, tab bar,
// email fields, markdown toolbar, find bar, editor/highlight/line-number
// layers, preview panes (markdown/CSV/HTML/PDF), action footer, version
// panel, mobile footer. Extracted from document.js's openPanel() to shrink
// that function; this is pure markup with no interpolated values, so a plain
// exported string constant is the whole module — openPanel() still does
// every bit of the wiring (event listeners, per-language show/hide, ...)
// against the ids this template defines.
export const DOC_PANE_TEMPLATE = `
      <input type="hidden" id="doc-title-input" value="" />
      <div class="doc-mobile-grabber" id="doc-mobile-grabber" aria-hidden="true"></div>
      <div class="doc-editor-header" id="doc-editor-actions">
        <button id="doc-undo-btn" class="doc-action-icon-btn" title="Undo (Ctrl+Z)" style="gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg><span style="font-size:11px;">Undo</span></button>
        <button id="doc-header-preview-btn" class="doc-action-icon-btn" title="Run / Preview" style="display:none;opacity:0.85;gap:4px;"></button>
        <span id="doc-stream-indicator" class="doc-stream-indicator" style="display:none"><span class="doc-stream-dot"></span> editing</span>
        <span id="doc-version-badge" class="doc-version-badge" title="Version history" style="display:none">v1</span>
        <span style="flex:1"></span>
        <button id="doc-split-btn" data-ws-split class="doc-action-icon-btn" title="Split screen with another feature" style="display:none;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="12" y1="4" x2="12" y2="20"/></svg></button>
        <button id="doc-export-pdf-btn" class="doc-action-icon-btn" title="Export PDF" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 18 15 15"/></svg> <span style="font-size:11px;">Export PDF</span></button>
        <button id="doc-pdf-view-btn" class="doc-action-icon-btn" title="Toggle PDF view" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> <span style="font-size:11px;">PDF</span></button>
        <select id="doc-language-select" class="doc-language-select">
          <option value="python">python</option>
          <option value="javascript">javascript</option>
          <option value="typescript">typescript</option>
          <option value="html">html</option>
          <option value="css">css</option>
          <option value="markdown">markdown</option>
          <option value="json">json</option>
          <option value="yaml">yaml</option>
          <option value="bash">bash</option>
          <option value="sql">sql</option>
          <option value="rust">rust</option>
          <option value="go">go</option>
          <option value="java">java</option>
          <option value="c">c</option>
          <option value="cpp">c++</option>
          <option value="csharp">c#</option>
          <option value="xml">xml</option>
          <option value="svg">svg</option>
          <option value="toml">toml</option>
          <option value="ini">ini</option>
          <option value="ruby">ruby</option>
          <option value="php">php</option>
          <option value="csv">csv</option>
          <option value="email">email</option>
          <option value="pdf">pdf</option>
        </select>
        <!-- Close + Copy/Export moved to the bottom action footer (#doc-actions-footer)
             so regular docs match the email footer layout. -->
      </div>
      <div class="doc-tab-bar" id="doc-tab-bar"></div>
      <div id="doc-email-header" class="doc-email-header" style="display:none">
        <button type="button" id="doc-email-collapse-btn" class="doc-email-collapse-btn" title="Hide email fields" aria-expanded="true">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 15 12 9 18 15"/></svg>
          <span id="doc-email-collapse-summary" class="doc-email-collapse-summary">No recipient · No subject</span>
        </button>
        <div id="doc-email-fields" class="doc-email-fields">
          <div class="email-field" style="position:relative">
            <span class="email-field-prefix">To</span>
            <input type="text" id="doc-email-to" placeholder="recipient@example.com" autocomplete="off" />
            <div id="doc-email-to-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" id="doc-email-show-cc" class="email-cc-toggle" title="Show Cc/Bcc">Cc</button>
          </div>
          <div class="email-field" id="doc-email-cc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Cc</span>
            <input type="text" id="doc-email-cc" placeholder="cc@example.com, example2" autocomplete="off" />
            <div id="doc-email-cc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" id="doc-email-bcc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Bcc</span>
            <input type="text" id="doc-email-bcc" placeholder="bcc@example.com" autocomplete="off" />
            <div id="doc-email-bcc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" style="position:relative"><span class="email-field-prefix">Subject</span><input type="text" id="doc-email-subject" placeholder="" /></div>
          <div id="doc-email-attachments" class="email-attachments" style="display:none"></div>
          <div id="doc-email-compose-atts" class="email-compose-atts" style="display:none"></div>
        </div>
        <input type="hidden" id="doc-email-in-reply-to" />
        <input type="hidden" id="doc-email-references" />
        <input type="hidden" id="doc-email-source-uid" />
        <input type="hidden" id="doc-email-source-folder" />
        <input type="file" id="doc-email-file-input" multiple style="display:none" />
      </div>
      <input type="file" id="doc-md-image-input" accept="image/*" multiple style="display:none" />
      <div class="doc-md-toolbar" id="doc-md-toolbar" style="display:none">
        <div class="md-toolbar-items" id="md-toolbar-items">
          <span class="md-view-toggle" id="doc-md-view-toggle" style="display:none" role="group" aria-label="Edit or preview">
            <button type="button" class="md-view-opt" data-mdview="edit" title="Edit source (Ctrl+Alt+M to toggle)"><span class="md-view-label">Write</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
            <button type="button" class="md-view-opt" data-mdview="preview" title="Preview (Ctrl+Alt+M to toggle)"><span class="md-view-label">Preview</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
          </span>
          <span class="md-view-toggle" id="doc-render-view-toggle" style="display:none" role="group" aria-label="Code or run">
            <button type="button" class="md-view-opt" data-renderview="code" title="Edit code"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></button>
            <button type="button" class="md-view-opt" data-renderview="run" title="Run / Preview"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>
          </span>
          <button id="doc-email-ai-reply-btn" class="doc-action-icon-btn md-toolbar-email-only" type="button" title="Draft a reply with AI (fast + optional context)" style="display:none;align-items:center;gap:4px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="color:var(--accent, var(--red));flex-shrink:0;position:relative;top:-1px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg><span style="font-size:11px;">Reply</span></button>
          <button id="doc-fontsize-btn" class="doc-action-icon-btn" title="Font size" style="position:relative;width:28px;height:26px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7;"><path d="M4 7V4h16v3"/><path d="M12 4v16"/><path d="M8 20h8"/></svg><span class="doc-fontsize-levels"><i data-sz="s">S</i><i data-sz="m">M</i><i data-sz="l">L</i></span></button>
          <button id="doc-diff-toggle-btn" class="doc-action-icon-btn" title="Compare changes" style="opacity:0.7;display:none;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 12H2l5-5 5 5H9"/><path d="M19 12h3l-5 5-5-5h3"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-toolbar-edit-only" data-md="bold" title="Bold (Ctrl+B)"><b>B</b></button>
          <button type="button" class="md-toolbar-edit-only" data-md="italic" title="Italic (Ctrl+I)"><i>I</i></button>
          <button type="button" class="md-toolbar-edit-only" data-md="strike" title="Strikethrough"><s>S</s></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-dd-toggle md-toolbar-edit-only" data-dd="heading" title="Heading"><b>H</b><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-edit-only" data-dd="list" title="List"><span style="font-variant-numeric:tabular-nums;">1.</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-toolbar-edit-only" data-md="link" title="Link"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></button>
          <button type="button" id="md-toolbar-attach-btn" class="md-toolbar-attach-btn md-toolbar-edit-only" title="Insert image"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-email-hide md-toolbar-edit-only" data-dd="code" title="Code">\`<svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <span id="md-toolbar-emoji-slot" class="md-toolbar-edit-only"></span>
          <span class="md-toolbar-sep md-toolbar-pdf-only" style="display:none"></span>
          <button type="button" id="doc-pdf-add-text-btn" class="md-toolbar-pdf-only" title="Add text box (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg></button>
          <button type="button" id="doc-pdf-add-check-btn" class="md-toolbar-pdf-only" title="Add checkmark (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></button>
          <button type="button" id="doc-pdf-add-sign-btn" class="md-toolbar-pdf-only" title="Add signature (then click on PDF)" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3l6 6-9 9-3-3z"/><path d="M9 15l-3 1 1-3"/><path d="M4 18l3-3"/><path d="M3 20l3-3"/><path d="M5 22l3-3"/></svg><span class="doc-pdf-sign-label">sign</span></button>
          <button type="button" id="doc-pdf-refresh-btn" class="md-toolbar-pdf-only" title="Reload PDF view" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
        </div>
        <div class="md-toolbar-overflow-wrapper" id="md-toolbar-overflow-wrapper" style="display:none">
          <button class="md-toolbar-overflow-toggle" id="md-toolbar-overflow-toggle" title="More formatting"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
          <div class="md-toolbar-overflow-menu" id="md-toolbar-overflow-menu"></div>
        </div>
        <button type="button" class="md-scroll-arrow md-scroll-left" id="md-scroll-left" title="Scroll left" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
        <button type="button" class="md-scroll-arrow md-scroll-right" id="md-scroll-right" title="Scroll right" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
      </div>
      <div id="doc-find-bar" class="doc-find-bar" style="display:none">
        <input id="doc-find-input" class="doc-find-input" type="text" placeholder="Find..." />
        <span id="doc-find-count" class="doc-find-count"></span>
        <button id="doc-find-prev" class="doc-find-nav" title="Previous">&uarr;</button>
        <button id="doc-find-next" class="doc-find-nav" title="Next">&darr;</button>
        <button id="doc-find-close" class="doc-find-close" title="Close">&times;</button>
      </div>
      <div id="doc-editor-wrap" class="doc-editor-wrap">
        <div id="doc-line-numbers" class="doc-line-numbers">1</div>
        <pre id="doc-editor-highlight" class="doc-editor-highlight"><code id="doc-editor-code"></code></pre>
        <textarea id="doc-editor-textarea" class="doc-editor-textarea" placeholder="Document content..." spellcheck="false"></textarea>
      </div>
      <!-- WYSIWYG email body. In email mode this replaces the source editor:
           B/I/S act on the live text (execCommand), and on send its HTML becomes
           the email's HTML part. Its plain text is mirrored into the textarea so
           the existing send/draft/change-detection paths keep working. -->
      <div id="doc-email-richbody" class="doc-email-richbody" contenteditable="true" spellcheck="true" style="display:none" data-no-swipe-dismiss></div>
      <div id="doc-email-actions" class="doc-email-actions" style="display:none">
        <button id="doc-email-discard-btn" class="email-discard-btn" title="Close email" style="display:inline-flex;align-items:center;gap:5px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span>Close</span></button>
        <span style="flex:1"></span>
        <div class="email-send-split">
          <button type="button" id="doc-email-send-btn" class="email-send-btn email-send-main" title="Send email (Ctrl+Enter)" onpointerdown="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)" onmousedown="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)" onclick="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>Send</button>
          <button type="button" id="doc-email-send-caret" class="email-send-btn email-send-caret" title="More send options" aria-haspopup="true" aria-expanded="false" onpointerdown="window.odysseusEmailCaretIntent&&window.odysseusEmailCaretIntent(event)" onmousedown="window.odysseusEmailCaretIntent&&window.odysseusEmailCaretIntent(event)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>
          <div id="doc-email-more-menu" class="email-more-menu" style="display:none">
            <div class="dropdown-item-compact" id="doc-email-draft-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></span>Save Draft</div>
            <div class="dropdown-item-compact" id="doc-email-schedule-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>Schedule Send...</div>
            <div class="dropdown-item-compact" id="doc-email-unread-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg></span>Mark Unread</div>
          </div>
        </div>
      </div>
      <div id="doc-md-preview" class="doc-md-preview" style="display:none"></div>
      <div id="doc-csv-preview" class="doc-csv-preview" style="display:none"></div>
      <iframe id="doc-html-preview" class="doc-html-preview" sandbox="allow-scripts allow-modals" style="display:none"></iframe>
      <div id="doc-pdf-view" style="display:none;width:100%;flex:1;min-height:0;overflow:auto;background:#525659;padding:20px 0;position:relative;">
        <div id="doc-pdf-save-pill" style="display:none;position:absolute;top:8px;right:14px;padding:4px 10px;border-radius:12px;font-size:11px;z-index:5;pointer-events:none;background:transparent;color:transparent;"></div>
      </div>
      <!-- Action footer sits AFTER all the content/preview panes so it stays
           pinned to the bottom no matter which pane (editor / md-preview /
           csv / html / pdf) is the one growing to fill. -->
      <div id="doc-actions-footer" class="doc-email-actions">
        <span class="email-send-split" id="doc-copy-export-split">
          <button type="button" id="doc-footer-copy-btn" class="email-send-btn email-send-main" title="Save new version" data-mode="save"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save</button>
          <button type="button" id="doc-footer-export-btn" class="email-send-btn email-send-caret" title="Export as…" aria-label="Export options"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 15 12 9 18 15"/></svg></button>
        </span>
      </div>
      <div id="doc-version-panel" class="doc-version-panel hidden">
        <div class="doc-version-header">
          <span>Version History</span>
          <button id="doc-version-close" class="doc-action-icon-btn" title="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div id="doc-version-list" class="doc-version-list"></div>
      </div>
      <div id="doc-mobile-footer" class="doc-mobile-footer">
        <button id="doc-mobile-close" class="doc-mobile-footer-btn" type="button">Unlink</button>
        <span style="flex:1"></span>
        <button id="doc-mobile-copy" class="doc-mobile-footer-btn" type="button">Copy</button>
      </div>
    `;
