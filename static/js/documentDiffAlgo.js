// static/js/documentDiffAlgo.js
//
// Pure text-diffing algorithms for the document editor — line-level LCS diff
// (used by diff-mode review), a simple common-prefix/suffix diff, and a
// second line-level diff used by the streaming edit animation. Extracted
// from document.js to reduce its size. No shared module state; every
// function takes its inputs as arguments and returns a plain value.

/** Line-level LCS diff algorithm. Returns an array of
 *  { type: 'equal'|'insert'|'delete', line: string } entries. */
export function _computeLineDiff(oldText, newText) {
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');
  const m = oldLines.length, n = newLines.length;

  // Build LCS table
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = oldLines[i - 1] === newLines[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Backtrack to produce diff entries
  const entries = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      entries.push({ type: 'equal', line: oldLines[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      entries.push({ type: 'insert', line: newLines[j - 1] });
      j--;
    } else {
      entries.push({ type: 'delete', line: oldLines[i - 1] });
      i--;
    }
  }
  entries.reverse();
  return entries;
}

/** Group diff entries (from _computeLineDiff) into chunks (contiguous change blocks) */
export function _buildDiffChunks(entries) {
  const chunks = [];
  let chunkId = 0;
  let lineIdx = 0;
  let i = 0;
  while (i < entries.length) {
    const e = entries[i];
    if (e.type === 'equal') {
      lineIdx++;
      i++;
    } else {
      // Gather contiguous non-equal entries into a chunk
      const startLine = lineIdx;
      const oldLines = [], newLines = [];
      while (i < entries.length && entries[i].type !== 'equal') {
        if (entries[i].type === 'delete') oldLines.push(entries[i].line);
        else newLines.push(entries[i].line);
        i++;
      }
      chunks.push({
        id: chunkId++,
        oldLines,
        newLines,
        startLine,
        resolved: false,
        accepted: false,
      });
      lineIdx += oldLines.length + newLines.length;
    }
  }
  return chunks;
}

export function simpleDiff(oldText, newText) {
  let i = 0;
  const minLen = Math.min(oldText.length, newText.length);
  while (i < minLen && oldText[i] === newText[i]) i++;
  const prefixLen = i;

  let oj = oldText.length;
  let nj = newText.length;
  while (oj > prefixLen && nj > prefixLen && oldText[oj - 1] === newText[nj - 1]) {
    oj--; nj--;
  }

  return {
    prefixLen,
    oldMid: oldText.slice(prefixLen, oj),
    newMid: newText.slice(prefixLen, nj),
  };
}

/**
 * Compute a line-level diff between two texts.
 * Returns array of { type: 'same'|'del'|'add', text: string }
 */
export function lineDiff(oldText, newText) {
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');

  // Simple LCS-based diff (Myers-like, but O(n*m) for clarity)
  const m = oldLines.length, n = newLines.length;
  // For very large diffs, skip detailed diff
  if (m * n > 500000) return null;

  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (oldLines[i] === newLines[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  const result = [];
  let i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && oldLines[i] === newLines[j]) {
      result.push({ type: 'same', text: oldLines[i] });
      i++; j++;
    } else if (j < n && (i >= m || dp[i][j + 1] >= dp[i + 1][j])) {
      result.push({ type: 'add', text: newLines[j] });
      j++;
    } else {
      result.push({ type: 'del', text: oldLines[i] });
      i++;
    }
  }
  return result;
}
