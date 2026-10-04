"""Project instruction files for agent workspaces (ODYSSEUS.md / AGENTS.md / CLAUDE.md).

When an agent chat is bound to a workspace folder, Odysseus can load the
instruction files that tools like Claude Code and Codex read: one per
directory, from a trusted root down to the workspace, with whole-line
``@relative/path.md`` imports expanded. The result is appended to the
persona's system prompt, so a folder can carry its own operating manual
(modes, file conventions, session protocol) regardless of which model runs.

Trust model
-----------
Instruction files become *system* instructions, so they are only read from
folders the admin has explicitly trusted via the
``ODYSSEUS_TRUSTED_INSTRUCTION_ROOTS`` environment variable (os.pathsep or
comma separated). This plays the role of Claude Code's "trust this folder"
prompt: a workspace that is not under a trusted root (a freshly cloned repo,
say) never has its AGENTS.md injected. Imports are confined to the same
trusted root, must be ``.md`` files, are de-duplicated, depth-limited and the
whole result is size-capped.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TRUSTED_ROOTS_ENV = "ODYSSEUS_TRUSTED_INSTRUCTION_ROOTS"
# Checked in this order in every directory; the first one present wins.
# ODYSSEUS.md lets a folder give Odysseus its own (usually leaner) manual while
# Claude Code keeps reading CLAUDE.md and Codex AGENTS.md from the same folder.
INSTRUCTION_FILENAMES = ("ODYSSEUS.md", "AGENTS.md", "CLAUDE.md")
MAX_IMPORT_DEPTH = 4
MAX_TOTAL_CHARS = 60_000

_IMPORT_LINE_RE = re.compile(r"^@(?P<path>[^\s@][^\s]*\.md)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

# (workspace, root) -> (signature, rendered text)
_cache: Dict[Tuple[str, str], Tuple[Tuple, str]] = {}


def trusted_roots() -> List[str]:
    raw = os.environ.get(TRUSTED_ROOTS_ENV, "") or ""
    roots = []
    for part in re.split(r"[,%s]" % re.escape(os.pathsep), raw):
        part = part.strip()
        if not part:
            continue
        resolved = os.path.realpath(os.path.expanduser(part))
        # Never trust a filesystem root: every path would be "inside" it.
        if os.path.isdir(resolved) and os.path.dirname(resolved) != resolved:
            roots.append(resolved)
    return roots


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def trusted_root_for(workspace: Optional[str]) -> Optional[str]:
    """The innermost trusted root containing ``workspace``, or None."""
    if not workspace:
        return None
    ws = os.path.realpath(workspace)
    matches = [r for r in trusted_roots() if _within(ws, r)]
    return max(matches, key=len) if matches else None


def _instruction_file_in(directory: str) -> Optional[str]:
    for name in INSTRUCTION_FILENAMES:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def instruction_files(workspace: Optional[str]) -> List[str]:
    """Instruction files from the trusted root down to the workspace (outermost first)."""
    root = trusted_root_for(workspace)
    if not root:
        return []
    ws = os.path.realpath(workspace)
    chain = [ws]
    while chain[-1] != root:
        chain.append(os.path.dirname(chain[-1]))
    files = []
    for directory in reversed(chain):
        found = _instruction_file_in(directory)
        if found and _within(os.path.realpath(found), root):
            files.append(os.path.realpath(found))
    return files


def has_instructions(workspace: Optional[str]) -> bool:
    return bool(instruction_files(workspace))


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _expand_imports(text: str, base_dir: str, root: str, depth: int, seen: set) -> str:
    """Replace whole-line ``@path.md`` imports with the file's (expanded) content.

    Imports inside fenced code blocks are left alone, as are paths that leave
    the trusted root, aren't .md files, don't exist, or were already included.
    """
    out = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        m = None if in_fence else _IMPORT_LINE_RE.match(line.strip())
        if not m:
            out.append(line)
            continue
        target = os.path.realpath(os.path.join(base_dir, os.path.expanduser(m.group("path"))))
        if depth >= MAX_IMPORT_DEPTH or not _within(target, root) or not os.path.isfile(target):
            out.append(line)
            continue
        if target in seen:
            continue
        seen.add(target)
        try:
            body = _read(target)
        except OSError:
            out.append(line)
            continue
        body = _expand_imports(body, os.path.dirname(target), root, depth + 1, seen)
        rel = os.path.relpath(target, root)
        out.append(f"<!-- imported from {rel} -->\n{body.strip()}\n<!-- end {rel} -->")
    return "\n".join(out)


def _signature(paths: List[str]) -> Tuple:
    sig = []
    for p in paths:
        try:
            st = os.stat(p)
            sig.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((p, None, None))
    return tuple(sig)


def load_workspace_instructions(workspace: Optional[str]) -> str:
    """Rendered instructions for ``workspace``; empty string when none apply.

    Cached on the instruction files' mtimes; imported files are re-read on a
    cache miss, so edit the top-level file (or restart) to pick up a changed
    import immediately.
    """
    files = instruction_files(workspace)
    if not files:
        return ""
    root = trusted_root_for(workspace)
    key = (os.path.realpath(workspace), root)
    sig = _signature(files)
    cached = _cache.get(key)
    if cached and cached[0] == sig:
        return cached[1]

    seen = set(files)
    sections = []
    for path in files:
        try:
            body = _read(path)
        except OSError as exc:
            logger.warning("workspace instructions: cannot read %s: %s", path, exc)
            continue
        body = _expand_imports(body, os.path.dirname(path), root, 1, seen)
        rel = os.path.relpath(path, os.path.dirname(root)) if os.path.dirname(root) else path
        sections.append(f"### Instructions from `{rel}`\n\n{body.strip()}")

    rendered = ""
    if sections:
        rendered = (
            "## Project instructions\n"
            f"The active workspace `{os.path.realpath(workspace)}` ships its own instruction files. "
            "Follow them for all work in this workspace; when they conflict with general "
            "defaults, these win. Relative file paths in them are relative to the folder of the file they "
            "appear in; tool paths are relative to the workspace.\n\n"
            + "\n\n".join(sections)
        )
        if len(rendered) > MAX_TOTAL_CHARS:
            logger.warning("workspace instructions for %s truncated to %d chars", workspace, MAX_TOTAL_CHARS)
            rendered = rendered[:MAX_TOTAL_CHARS] + "\n\n[instructions truncated: size limit reached]"
    _cache[key] = (sig, rendered)
    return rendered
