"""Parse, verify and gate reviewer findings; keep per-PR round state.

  py -3 findings.py check --raw review.out --root <checkout> --ci pass|fail|pending|none \
      --repo owner/name --pr N|branch:<name> --sha <head> [--mode branch]
prints JSON: {summary, kept, dropped, verdict, round, action}
  action: wait | send-fix | escalate | ask-merge | ready-for-pr (branch mode)
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
DUP_WINDOW = 3  # lines; cross-engine duplicates only


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
    # Worst first, so a duplicate keeps the higher severity.
    rank = lambda i: SEVERITIES.index(i["severity"]) if isinstance(i, dict) and i.get("severity") in SEVERITIES else 9  # noqa: E731
    for it in sorted(items, key=rank):
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
            # Two engines (--engine both) word the same issue differently: same file, within 3 lines.
            other = any(k["file"] == it["file"] and abs(k["line"] - line) <= DUP_WINDOW
                        and it.get("engine") and k.get("engine") not in (None, it["engine"]) for k in kept)
            if key in seen or other:
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


def home():
    return Path(os.environ.get("PR_GATE_HOME") or Path.home() / ".pr-gate")


BRANCH_RE = re.compile(r"branch:([A-Za-z0-9._/-]{1,100})")


def pr_id(value):
    """A PR number (>= 1) or `branch:<name>` for a branch with no PR yet. Anything else,
    PR 0 included, is refused: every branch needs its own state and round cap."""
    if isinstance(value, int) and not isinstance(value, bool):
        n = value
    elif isinstance(value, str) and value.isdigit():
        n = int(value)
    else:
        m = BRANCH_RE.fullmatch(str(value))
        if not m or ".." in m.group(1) or m.group(1).startswith(("/", "-")):
            raise ValueError(f"not a PR number or branch:<name>: {value!r}")
        return f"branch:{m.group(1)}"
    if n < 1:
        raise ValueError("PR number must be 1 or more (use branch:<name> before a PR exists)")
    return n


def _slug(pr):
    pr = pr_id(pr)
    return str(pr) if isinstance(pr, int) else "b-" + re.sub(r"[^A-Za-z0-9_-]+", "_", pr[len("branch:"):])


def key(repo, pr):
    """File-name key for one PR, shared by state/, inbox/ and the inbox mod (mod/hooks/register.ts)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", repo.replace("/", "__")).strip("_") + f"__{_slug(pr)}"


def _state_path(repo, pr):
    d = home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key(repo, pr)}.json"


def load(repo, pr):
    p = _state_path(repo, pr)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"repo": repo, "pr": pr_id(pr), "rounds": []}


def record(repo, pr, sha, ci, verdict, counts, kept=()):
    """One round per head SHA: re-checking the same SHA replaces its round."""
    st = load(repo, pr)
    entry = {"sha": sha, "ci": ci, "verdict": verdict, "counts": counts,
             "findings": [{k: i.get(k) for k in ("severity", "file", "line", "title")} for i in kept]}
    if st["rounds"] and st["rounds"][-1]["sha"] == sha:
        st["rounds"][-1] = entry
    else:
        st["rounds"].append(entry)
    _state_path(repo, pr).write_text(json.dumps(st, indent=2), encoding="utf-8")
    return st


def _followups_path(repo):
    d = home() / "followups"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^A-Za-z0-9_-]+", "_", repo.replace("/", "__")).strip("_") + ".json")


def followups(repo, include_closed=False):
    """Mediums left behind when a PR went green at the round cap; carried into every later review."""
    p = _followups_path(repo)
    items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    return items if include_closed else [i for i in items if not i.get("closed_by")]


def _save_followups(repo, items):
    _followups_path(repo).write_text(json.dumps(items, indent=2), encoding="utf-8")


def add_followups(repo, pr, sha, kept):
    items = followups(repo, include_closed=True)
    pr = pr_id(pr)
    known = {(i["pr"], i["file"], i["title"]) for i in items}
    n = sum(1 for i in items if i["pr"] == pr)
    added = []
    for k in kept:
        if (pr, k.get("file"), k.get("title")) in known:
            continue  # same SHA re-checked
        n += 1
        added.append({"id": f"{_slug(pr)}-{n}", "pr": pr, "sha": sha,
                      **{f: k.get(f) for f in ("severity", "file", "line", "title", "fix")}})
    _save_followups(repo, items + added)
    return added


def close_followups(repo, ids, by):
    items = followups(repo, include_closed=True)
    closed = []
    for i in items:
        if i["id"] in ids and not i.get("closed_by"):
            i["closed_by"] = by
            closed.append(i["id"])
    _save_followups(repo, items)
    return closed


def next_action(st, mode="pr"):
    last = st["rounds"][-1]
    if last["verdict"] == "wait":
        return "wait"
    if last["verdict"] == "green":
        return "ready-for-pr" if mode == "branch" else "ask-merge"
    return "escalate" if len(st["rounds"]) >= MAX_ROUNDS else "send-fix"


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--raw", required=True)
    c.add_argument("--root", required=True)
    c.add_argument("--ci", required=True, choices=["pass", "fail", "pending", "none"])
    c.add_argument("--repo", required=True)
    c.add_argument("--pr", required=True, type=pr_id)
    c.add_argument("--sha", required=True)
    c.add_argument("--mode", choices=["pr", "branch"], default="pr")
    s = sub.add_parser("state")
    s.add_argument("--repo", required=True)
    s.add_argument("--pr", required=True, type=pr_id)
    f = sub.add_parser("followups")
    f.add_argument("--repo", required=True)
    f.add_argument("--close", nargs="*", default=[], help="ids fixed elsewhere (by hand)")
    a = ap.parse_args(argv)
    if a.cmd == "state":
        print(json.dumps(load(a.repo, a.pr), indent=2))
        return 0
    if a.cmd == "followups":
        if a.close:
            close_followups(a.repo, set(a.close), "hand")
        print(json.dumps(followups(a.repo), indent=2))
        return 0
    try:
        out = extract(Path(a.raw).read_text(encoding="utf-8", errors="replace"))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    kept, dropped = verify(out.get("findings", []), a.root)
    verdict = gate(a.ci, kept)
    counts = {s: sum(1 for k in kept if k["severity"] == s) for s in SEVERITIES}
    round_no = len([r for r in load(a.repo, a.pr)["rounds"] if r["sha"] != a.sha]) + 1
    added = []
    # At the cap, a round with only mediums/lows and green CI goes to the merge report; the
    # mediums become follow-ups that every later review of this repo sees until one fixes them.
    if verdict == "fix" and a.ci in ("pass", "none") and round_no >= MAX_ROUNDS and not (counts["blocker"] or counts["high"]):
        verdict = "green"
        added = add_followups(a.repo, a.pr, a.sha, [k for k in kept if k["severity"] == "medium"])
    resolved = [str(r) for r in out.get("resolved") or [] if isinstance(r, (str, int))]
    by = f"#{a.pr}" if isinstance(a.pr, int) else a.pr
    closed = close_followups(a.repo, set(resolved), by) if resolved else []
    st = record(a.repo, a.pr, a.sha, a.ci, verdict, counts, kept)
    print(json.dumps({"summary": out.get("summary", ""), "kept": kept, "dropped": dropped,
                      "verdict": verdict, "round": len(st["rounds"]),
                      "action": next_action(st, a.mode), "followups_added": added,
                      "followups_closed": closed, "followups_open": followups(a.repo)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
