// static/js/documentCsv.js
//
// CSV parsing/serialization for the document editor's CSV table preview.
// Extracted from document.js to reduce its size — pure functions, no shared
// module state, only used by toggleCsvPreview() (still in document.js).

/** Parse CSV text into a 2D array (handles quoted fields) */
export function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') { inQuotes = false; }
      else { field += ch; }
    } else {
      if (ch === '"') { inQuotes = true; }
      else if (ch === ',') { row.push(field); field = ''; }
      else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
        if (ch === '\r') i++;
        row.push(field); field = '';
        if (row.some(c => c.trim())) rows.push(row);
        row = [];
      } else { field += ch; }
    }
  }
  row.push(field);
  if (row.some(c => c.trim())) rows.push(row);
  return rows;
}

/** Escape a CSV field (quote if it contains comma, quote, or newline) */
export function csvEscapeField(val) {
  if (val.includes(',') || val.includes('"') || val.includes('\n')) {
    return '"' + val.replace(/"/g, '""') + '"';
  }
  return val;
}

/** Rebuild CSV text from the live table DOM */
export function syncTableToTextarea(preview, textarea) {
  const table = preview.querySelector('.csv-table');
  if (!table) return;
  const lines = [];
  // Header
  const ths = table.querySelectorAll('thead th');
  if (ths.length) lines.push([...ths].map(th => csvEscapeField(th.textContent)).join(','));
  // Body
  table.querySelectorAll('tbody tr').forEach(tr => {
    const cells = [...tr.querySelectorAll('td')].map(td => csvEscapeField(td.textContent));
    lines.push(cells.join(','));
  });
  textarea.value = lines.join('\n') + '\n';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}
