"""
extensions/ithaca/ai_tile_builder.py

Single-shot AI tile proposal: given a Postgres connection, a free-form
context doc the user pastes (notes about *that* dataset — nothing here
assumes any particular table), and a natural-language ask, produce one
TileConfig-shaped JSON draft for the user to preview/edit/save.

Deliberately NOT wired into the general agent tool-calling framework
(src/agent_loop.py, src/tools/*) — this is a dedicated backend function that
calls the LLM once, per the roadmap's v1 recommendation. The three
underlying operations (introspect, test-query, propose) are kept as
separate functions so a later upgrade to a real tool-calling loop (letting
the model see query errors and self-correct) doesn't require re-deriving
this logic — see the module docstring discussion in the project plan.

Reliability strategy (tuned for a 7-10B local model, not just a frontier
one) — two complementary layers, since prompting alone can't guarantee
compliance:
  1. Ask the model for the SMALLEST possible JSON object — just
     title/query/viz_type/label_field/value_field. Everything else
     (schema_version, id, data_source wrapper, refresh_interval_seconds,
     created_by, and — for table tiles — the columns list) is assembled in
     Python from data the model never has to get right. Fewer fields the
     model must produce correctly means fewer ways for it to fail, and it
     shrinks both the prompt (a terser expected-output description) and the
     completion (less to generate).
  2. A deterministic repair pass (_sanitize_query) fixes the single most
     common failure mode observed in testing — an unquoted multi-word
     `AS Some Label` alias, which is invalid SQL — regardless of whether the
     model followed the snake_case-alias rule in the prompt. Belt AND
     suspenders: the rule reduces how often this happens, the repair pass
     catches it when the rule is ignored anyway.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from core.external_db import introspect_schema, run_readonly_query, ExternalDbError
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async

from extensions.ithaca.tile_schema import TileConfig, TileColumn

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_VIZ_TYPES = {"table", "stat", "bar", "line", "pie", "list"}

_MAX_SCHEMA_TABLES = 40  # cap prompt size against a DB with a huge public schema

# Matches `AS <bare identifier(s)>` up to the next SQL keyword/comma/paren/EOS
# — used to auto-fix an unquoted multi-word alias (invalid SQL) into snake_case
# regardless of whether the model obeyed the prompt's alias rule.
_ALIAS_STOP = r'(?:,|\)|\bFROM\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|$)'
_BARE_ALIAS_RE = re.compile(rf'\bAS\s+(?!")([A-Za-z_][A-Za-z0-9_ ]*?)(?=\s*{_ALIAS_STOP})', re.IGNORECASE)


class TileProposalError(Exception):
    """User-facing error for a failed AI tile proposal."""


# One fixed, minimal few-shot example. A 7-10B model reproduces an output
# shape it's just seen far more reliably than one only described in prose —
# worth the token cost here specifically because it's the anchor for a
# "near 100% success" goal, not a nice-to-have.
_EXAMPLE_SCHEMA = "- public.app_config(app_name text, update_action text)"
_EXAMPLE_INSTRUCTION = "count apps by update_action as a bar chart"
_EXAMPLE_OUTPUT = (
    '{"title":"Apps by Update Action",'
    '"query":"SELECT update_action AS action, count(*) AS cnt FROM public.app_config GROUP BY update_action",'
    '"viz_type":"bar","label_field":"action","value_field":"cnt"}'
)

_SYSTEM_PROMPT = f"""Generate ONE dashboard tile as a single-line JSON object. Output ONLY the JSON — no markdown, no prose, no explanation, before or after.

Required keys: title, query, viz_type.
Optional keys: label_field, value_field (only when viz_type is bar/line/pie/stat — omit otherwise).

Rules:
1. query = one SELECT or WITH...SELECT statement. No semicolon. No INSERT/UPDATE/DELETE/DROP/ALTER/CREATE. Only tables/columns from the schema given.
2. Every selected column MUST have a short snake_case alias via `AS alias_name` — one word, letters/digits/underscores only. NEVER a multi-word alias like `AS Deployed Version` (invalid SQL).
3. viz_type is exactly one of: table, stat, bar, line, pie, list.
   - table (default): multiple columns of rows.
   - list: a single column of rows.
   - stat: one aggregate number.
   - bar/line/pie: label_field and value_field MUST name aliases from your own query.
4. Do not invent tables/columns not in the schema below.

Example —
Schema:
{_EXAMPLE_SCHEMA}
Request: {_EXAMPLE_INSTRUCTION}
Output: {_EXAMPLE_OUTPUT}
"""


def _format_schema(tables: list[dict]) -> str:
    lines = [f"- {t['schema']}.{t['table']}({', '.join(f'{c['name']} {c['type']}' for c in t['columns'])})"
              for t in tables[:_MAX_SCHEMA_TABLES]]
    if len(tables) > _MAX_SCHEMA_TABLES:
        lines.append(f"(+{len(tables) - _MAX_SCHEMA_TABLES} more tables not shown)")
    return "\n".join(lines) or "(no tables visible to this connection's role)"


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = _JSON_FENCE_RE.search(text) or _JSON_OBJECT_RE.search(text)
    candidate = m.group(1) if (m and m.re is _JSON_FENCE_RE) else (m.group(0) if m else text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise TileProposalError(f"Model did not return valid JSON: {exc}") from exc


def _sanitize_query(sql: str) -> str:
    """Rewrite any unquoted multi-word `AS alias` into snake_case. A
    best-effort regex repair, not a SQL parser — deliberately narrow
    (anchored on `AS` + stopword lookahead) so it only ever touches the
    exact shape that's actually invalid, not e.g. `FROM foo AS bar_baz`
    which is already fine."""
    def _fix(m: re.Match) -> str:
        alias = m.group(1).strip()
        if " " not in alias:
            return m.group(0)
        return f"AS {re.sub(r'\s+', '_', alias).lower()}"
    return _BARE_ALIAS_RE.sub(_fix, sql.strip())


def _humanize(field: str) -> str:
    return " ".join(w.capitalize() for w in field.replace("-", "_").split("_")) or field


async def propose_tile_config(
    tile_id: str, connection_ref: str, instruction: str, context_doc: str = "",
    owner: Optional[str] = None,
) -> dict:
    """Introspect the connection, ask the LLM for a minimal tile draft,
    assemble + validate the full TileConfig, and return it (unsaved)."""
    if not instruction.strip():
        raise TileProposalError("instruction is required")
    try:
        tables = await introspect_schema(connection_ref)
    except ExternalDbError as exc:
        raise TileProposalError(f"Could not introspect connection '{connection_ref}': {exc}") from exc

    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        url, model, headers = resolve_endpoint("default", owner=owner)
    if not url or not model:
        raise TileProposalError(
            "No LLM endpoint configured — set a Utility or Default Chat model in Settings → AI Defaults."
        )

    user_parts = [f"Schema:\n{_format_schema(tables)}"]
    if context_doc.strip():
        user_parts.append(f"Notes:\n{context_doc.strip()}")
    user_parts.append(f"Request: {instruction.strip()}")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    # Small, deterministic completion: this is a fixed-shape extraction task,
    # not open-ended generation — temperature 0 and a tight token cap both
    # reduce the chance of the model wandering off-format.
    raw = await llm_call_async(url, model, messages, headers=headers, max_tokens=400, temperature=0.0)
    if not raw:
        raise TileProposalError("LLM returned no content")

    proposed = _extract_json(raw)
    title = str(proposed.get("title") or instruction.strip()[:60]).strip()
    query = _sanitize_query(str(proposed.get("query") or ""))
    viz_type = str(proposed.get("viz_type") or "table").strip().lower()
    if viz_type not in _VIZ_TYPES:
        raise TileProposalError(f"Model returned an unknown viz_type: {viz_type!r}")
    if not query:
        raise TileProposalError("Model did not return a query")

    full = {
        "schema_version": 1,
        "id": tile_id,
        "title": title,
        "data_source": {"type": "postgres", "connection_ref": connection_ref, "query": query},
        "viz": {
            "type": viz_type,
            "columns": [],
            "label_field": proposed.get("label_field") or None,
            "value_field": proposed.get("value_field") or None,
        },
        "refresh_interval_seconds": 900,
        "created_by": "ai_tile_builder",
        "notes": "",
    }
    try:
        cfg = TileConfig.model_validate(full)
    except Exception as exc:
        raise TileProposalError(f"Model's proposed tile config failed validation: {exc}") from exc

    # Sanity-check the proposed query actually runs before handing it back —
    # a syntax error surfaces here as a clear message instead of at Preview.
    # For table/list tiles this doubles as the source of `columns`: better to
    # derive labels from what the query actually returned than to also ask
    # the model to enumerate them (one less thing it can get wrong or let
    # drift out of sync with its own query).
    try:
        result = run_readonly_query(cfg.data_source.connection_ref, cfg.data_source.query, row_limit=1)
    except ExternalDbError as exc:
        raise TileProposalError(f"Proposed query failed to run: {exc}") from exc

    if cfg.viz.type == "table":
        cfg.viz.columns = [TileColumn(field=c, label=_humanize(c)) for c in result["columns"]]

    return cfg.model_dump()
