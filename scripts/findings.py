"""Parse, verify and gate reviewer findings; keep per-PR round state.

  py -3 findings.py check --raw review.out --root <checkout> --ci pass|fail|pending|none \
      --repo owner/name --pr N --sha <head>
prints JSON: {summary, kept, dropped, verdict, round, action}
  action: wait | send-fix | escalate | ask-merge
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

SEVERITIES = ("blocker", "high", "medium", "low")
BLOCKING = {"blocker", "high", "medium"}
MAX_ROUNDS = 3
MIN_SCENARIO = 25  # shorter than this is "might be an issue", not a failure scenario


def _first_json(text):
    """Last JSON object in text (fenced or bare) that has a findings list."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    start = text.find("{")
    if start != -1:
        candidates.append(text[start:text.rfind("}") + 1])
    for c in reversed(candidates):
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
            return obj
    return None


def extract(raw):
    """Reviewer output -> {"summary", "findings"}. Understands claude -p json,
    codex --json JSONL and plain text with a fenced JSON block."""
    raw = raw.strip()
    texts = [raw]
    try:
        env = json.loads(raw)
        if isinstance(env, dict):
            if isinstance(env.get("findings"), list):
                return env
            for k in ("result", "structured_output", "text"):
                v = env.get(k)
                if isinstance(v, dict) and isinstance(v.get("findings"), list):
                    return v
                if isinstance(v, str):
                    texts.insert(0, v)
    except ValueError:
        for line in raw.splitlines():  # codex JSONL: keep agent messages, last wins
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            item = ev.get("item") if isinstance(ev, dict) else None
            if isinstance(item, dict) and item.get("type") == "agent_message":
                texts.insert(0, item.get("text", ""))
    for t in texts:
        obj = _first_json(t)
        if obj is not None:
            return obj
    raise ValueError("no findings JSON in reviewer output")


def verify(items, root):
    """Keep only findings that point at a real line in the PR head and name a
    concrete failure scenario. Returns (kept, dropped-with-reason)."""
    root = Path(root).resolve()
    kept, dropped, seen = [], [], set()
    for it in items:
        it = dict(it) if isinstance(it, dict) else {}
        reason = None
        file, line = str(it.get("file", "")), it.get("line")
        m = re.fullmatch(r"(.+?):(\d+)", file)
        if m and line in (None, ""):
            file, line = m.group(1), m.group(2)
        try:
            line = int(line)
        except (TypeError, ValueError):
            line = None
        path = (root / file).resolve() if file else None
        if it.get("severity") not in SEVERITIES:
            reason = "bad severity"
        elif not file or path is None or root not in path.parents:
            reason = "file outside repo"
        elif not path.is_file():
            reason = "file not in PR head"
        elif line is None or line < 1:
            reason = "no line"
        elif line > len(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            reason = "line past end of file"
        elif len(str(it.get("scenario", "")).strip()) < MIN_SCENARIO:
            reason = "no concrete failure scenario"
        elif not str(it.get("fix", "")).strip():
            reason = "no fix"
        else:
            it["file"], it["line"] = file.replace("\\", "/"), line
            key = (it["file"], line, it.get("title", ""))
            if key in seen:
                reason = "duplicate"
            else:
                seen.add(key)
                kept.append(it)
                continue
        it["reason"] = reason
        dropped.append(it)
    kept.sort(key=lambda x: SEVERITIES.index(x["severity"]))
    return kept, dropped


def gate(ci, items):
    if ci == "pending":
        return "wait"
    if ci == "fail" or any(i.get("severity") in BLOCKING for i in items):
        return "fix"
    return "green"


def _state_path(repo, pr):
    home = Path(os.environ.get("PR_GATE_HOME") or Path.home() / ".pr-gate")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", repo.replace("/", "__")).strip("_")
    d = home / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}__{int(pr)}.json"


def load(repo, pr):
    p = _state_path(repo, pr)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"repo": repo, "pr": int(pr), "rounds": []}


def record(repo, pr, sha, ci, verdict, counts):
    """One round per head SHA: re-checking the same SHA replaces its round."""
    st = load(repo, pr)
    entry = {"sha": sha, "ci": ci, "verdict": verdict, "counts": counts}
    if st["rounds"] and st["rounds"][-1]["sha"] == sha:
        st["rounds"][-1] = entry
    else:
        st["rounds"].append(entry)
    _state_path(repo, pr).write_text(json.dumps(st, indent=2), encoding="utf-8")
    return st


def next_action(st):
    last = st["rounds"][-1]
    if last["verdict"] == "wait":
        return "wait"
    if last["verdict"] == "green":
        return "ask-merge"
    return "escalate" if len(st["rounds"]) >= MAX_ROUNDS else "send-fix"


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--raw", required=True)
    c.add_argument("--root", required=True)
    c.add_argument("--ci", required=True, choices=["pass", "fail", "pending", "none"])
    c.add_argument("--repo", required=True)
    c.add_argument("--pr", required=True, type=int)
    c.add_argument("--sha", required=True)
    s = sub.add_parser("state")
    s.add_argument("--repo", required=True)
    s.add_argument("--pr", required=True, type=int)
    a = ap.parse_args(argv)
    if a.cmd == "state":
        print(json.dumps(load(a.repo, a.pr), indent=2))
        return 0
    try:
        out = extract(Path(a.raw).read_text(encoding="utf-8", errors="replace"))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    kept, dropped = verify(out.get("findings", []), a.root)
    verdict = gate(a.ci, kept)
    counts = {s: sum(1 for k in kept if k["severity"] == s) for s in SEVERITIES}
    st = record(a.repo, a.pr, a.sha, a.ci, verdict, counts)
    print(json.dumps({"summary": out.get("summary", ""), "kept": kept, "dropped": dropped,
                      "verdict": verdict, "round": len(st["rounds"]),
                      "action": next_action(st)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
