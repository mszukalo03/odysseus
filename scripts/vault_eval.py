#!/usr/bin/env python3
"""Ask a running Odysseus a fixed set of questions and score the answers.

Built for vault personas (a workspace + project instructions) but works for
any agent chat. For each case it opens a fresh session, streams one message
through ``/api/chat_stream`` and reports whether the answer matched, how many
tools and rounds it took, time to first token and total time. Run it before
and after a change, or once per model, to compare.

Auth uses a browser session cookie, not an API token: bearer tokens are capped
at the non-admin tool policy, which hides the file tools a vault persona
needs. Copy the ``odysseus_session`` cookie from the browser's dev tools.

    ODY_SESSION=... ./venv/bin/python scripts/vault_eval.py cases.yaml \\
        --base-url http://localhost:7000 --endpoint-id ab12cd34 --model qwen3.5-9b

Cases file (YAML or JSON):

    defaults:              # optional, merged into every case
      mode: agent          # agent | chat
      preset_id: custom    # the active persona (Character -> Persona)
      workspace: /vaults/omegaV2/thesis
      thinking: off        # auto | off | low | medium | high (needs thinking control)
    cases:
      - name: reading-queue-count
        question: How many papers are in my reading queue?
        expect: "\\b2[67]\\b"          # regex, case-insensitive
        max_tools: 4                  # optional: flag runs that needed more

Exit status is 1 when any case fails, so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

COOKIE_NAME = "odysseus_session"


def load_cases(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            sys.exit("PyYAML is not installed; use a .json cases file or `pip install pyyaml`.")
        data = yaml.safe_load(raw) or {}
    else:
        data = json.loads(raw)
    if isinstance(data, list):
        data = {"cases": data}
    defaults = data.get("defaults") or {}
    cases = []
    for i, case in enumerate(data.get("cases") or []):
        merged = {**defaults, **case}
        merged.setdefault("name", f"case-{i + 1}")
        if not merged.get("question"):
            sys.exit(f"case {merged['name']!r} has no question")
        cases.append(merged)
    return cases


def create_session(client: httpx.Client, args, name: str) -> str:
    resp = client.post(
        "/api/session",
        data={"name": f"eval: {name}", "endpoint_id": args.endpoint_id or "", "model": args.model or ""},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def run_case(client: httpx.Client, args, case: Dict[str, Any]) -> Dict[str, Any]:
    session_id = create_session(client, args, case["name"])
    form = {
        "message": case["question"],
        "session": session_id,
        "mode": case.get("mode", "agent"),
    }
    for key in ("preset_id", "workspace", "thinking"):
        if case.get(key):
            form[key] = str(case[key])
    if args.endpoint_id:
        form["selected_endpoint_id"] = args.endpoint_id
    if args.model:
        form["selected_model"] = args.model

    answer, tools, rounds, events = [], 0, 1, []
    t0 = time.monotonic()
    ttft: Optional[float] = None
    with client.stream("POST", "/api/chat_stream", data=form, timeout=args.timeout) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                break
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if isinstance(payload.get("delta"), str):
                if payload.get("thinking"):
                    continue
                if ttft is None:
                    ttft = time.monotonic() - t0
                answer.append(payload["delta"])
                continue
            kind = payload.get("type")
            if kind == "tool_start":
                tools += 1
            elif kind == "agent_step":
                rounds = max(rounds, int(payload.get("round") or rounds))
            elif kind in {"rounds_exhausted", "budget_exceeded", "loop_breaker_triggered", "error"}:
                events.append(kind)
    total = time.monotonic() - t0
    text = "".join(answer).strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    expect = case.get("expect")
    matched = bool(re.search(expect, text, re.I)) if expect else bool(text)
    too_many = case.get("max_tools") is not None and tools > int(case["max_tools"])
    return {
        "name": case["name"],
        "ok": matched and not too_many,
        "matched": matched,
        "tools": tools,
        "rounds": rounds,
        "ttft": ttft,
        "total": total,
        "events": events,
        "answer": text,
        "session": session_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", help="YAML or JSON cases file")
    ap.add_argument("--base-url", default=os.environ.get("ODY_BASE_URL", "http://localhost:7000"))
    ap.add_argument("--session-cookie", default=os.environ.get("ODY_SESSION", ""),
                    help=f"value of the {COOKIE_NAME} cookie (or set ODY_SESSION)")
    ap.add_argument("--endpoint-id", default="", help="model endpoint id to run on (default: app default)")
    ap.add_argument("--model", default="", help="model name on that endpoint")
    ap.add_argument("--only", default="", help="run only cases whose name contains this")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--json", dest="json_out", default="", help="also write full results to this file")
    ap.add_argument("--show-answers", action="store_true")
    args = ap.parse_args()

    if not args.session_cookie:
        sys.exit(f"Set ODY_SESSION (the {COOKIE_NAME} cookie from a logged-in browser).")
    cases = [c for c in load_cases(args.cases) if args.only in c["name"]]
    if not cases:
        sys.exit("no cases to run")

    results = []
    with httpx.Client(base_url=args.base_url.rstrip("/"),
                      cookies={COOKIE_NAME: args.session_cookie},
                      timeout=args.timeout) as client:
        for case in cases:
            try:
                res = run_case(client, args, case)
            except httpx.HTTPError as exc:
                res = {"name": case["name"], "ok": False, "matched": False, "tools": 0, "rounds": 0,
                       "ttft": None, "total": 0.0, "events": [f"http: {exc}"], "answer": ""}
            results.append(res)
            ttft = f"{res['ttft']:.1f}s" if res.get("ttft") is not None else "-"
            flag = "PASS" if res["ok"] else "FAIL"
            extra = f"  [{', '.join(res['events'])}]" if res["events"] else ""
            print(f"{flag}  {res['name']:<32} tools={res['tools']:<3} rounds={res['rounds']:<3} "
                  f"ttft={ttft:<7} total={res['total']:.1f}s{extra}")
            if args.show_answers or not res["ok"]:
                snippet = res["answer"][:400].replace("\n", " ")
                print(f"      answer: {snippet or '(empty)'}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} passed"
          + (f" ({args.model} @ {args.endpoint_id})" if args.model or args.endpoint_id else ""))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
