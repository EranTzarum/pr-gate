"""Append and summarize per-review usage from lean_run.

  py -3 usage.py [--since 5h|today|week|all] [--json]

Every review is also appended to the machine-wide shared file ~/.agent-usage/usage.jsonl
(contract in its README, shared with an orchestrator): one `spend` row per run, plus
`server` rows for the wallet's own % when the engine reports it (codex 5h/week). The Codex
budget reads the newest `server` rows from that file too, so other tools' use counts.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import findings  # noqa: E402


def PATH():
    return findings.home() / "usage.jsonl"


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _aware_utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def SHARED():
    """Machine-wide file. An isolated PR_GATE_HOME (tests, trial runs) keeps its own copy."""
    if os.environ.get("AGENT_USAGE_HOME"):
        return Path(os.environ["AGENT_USAGE_HOME"]) / "usage.jsonl"
    if os.environ.get("PR_GATE_HOME"):
        return Path(os.environ["PR_GATE_HOME"]) / "agent-usage" / "usage.jsonl"
    return Path.home() / ".agent-usage" / "usage.jsonl"


CURSOR_INCLUDED = ("composer-", "grok-", "cursor-grok-")


def pool_of(engine, model):
    model = model or ""
    if engine == "codex":
        return "codex:chatgpt"
    if engine == "claude":
        return "claude:max"
    if engine == "cursor":
        return "cursor:included" if model.startswith(CURSOR_INCLUDED) else "cursor:other"
    if engine == "agy":
        if model.startswith("gemini-4-argon"):
            return "agy:argon"  # its own weekly limit (seats/seats.json)
        return "agy:gemini" if model.startswith("gemini-") else "agy:claude-gpt"
    return f"{engine}:unknown"


def _spend_usage(u):
    """Each engine's usage dict -> the shared {input, cached, cache_creation, output}."""
    u = u or {}
    pick = lambda *keys: next((u[k] for k in keys if isinstance(u.get(k), (int, float))), 0)  # noqa: E731
    return {"input": pick("input_tokens", "inputTokens"),
            "cached": pick("cached_input_tokens", "cache_read_input_tokens", "cacheReadTokens", "cache_read_tokens"),
            "cache_creation": pick("cache_creation_input_tokens", "cacheWriteTokens"),
            "output": pick("output_tokens", "outputTokens")}


def _append(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line)  # one write per row (contract)


def share(rec):
    """Shared rows for one run. Numbers, names and ids only."""
    engine, model = rec.get("engine"), rec.get("model")
    if not engine:
        return
    pool, now = pool_of(engine, model), _now_iso()
    rows = [{"ts": rec.get("ts") or now, "source": "pr-gate", "kind": "spend", "engine": engine,
             "pool": pool, "model": model, "run_id": rec.get("sha"), "ticket": f"{rec.get('repo')}#{rec.get('pr')}",
             "usage": _spend_usage(rec.get("usage"))}]
    rl = rec.get("rate_limits") or {}
    for window in ("5h", "week"):
        if isinstance(rl.get(window), (int, float)):
            row = {"ts": rec.get("ts") or now, "source": "pr-gate", "kind": "server", "engine": engine,
                   "pool": pool, "window": window, "used_pct": float(rl[window]), "read_at": now}
            if isinstance(rl.get(f"{window}_resets_at"), (int, float)):
                row["resets_at"] = rl[f"{window}_resets_at"]
            rows.append(row)
    _append(SHARED(), [json.dumps(r, separators=(",", ":")) + "\n" for r in rows])


def log(record: dict) -> None:
    rec = dict(record)
    if "ts" not in rec:
        rec["ts"] = _now_iso()
    line = json.dumps(rec, separators=(",", ":")) + "\n"
    try:
        _append(PATH(), [line])
    except OSError as exc:
        print(f"warning: usage log write failed: {exc}", file=sys.stderr)
    try:
        share(rec)
    except OSError as exc:
        print(f"warning: shared usage write failed: {exc}", file=sys.stderr)


def _jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # includes a partial last line
        if isinstance(rec, dict):
            out.append(rec)
    return out


def read(since_seconds=None):
    p = PATH()
    if not p.exists():
        return []
    cutoff = None
    if since_seconds is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff is not None:
            ts = _parse_ts(rec.get("ts"))
            if ts is None:
                continue
            if _aware_utc(ts) <= cutoff:
                continue
        out.append(rec)
    return out


def last_limits(engine):
    """Newest reading per window for this engine: our own runs, plus the shared file's
    pool-wide `server` rows from any tool. Returns {5h, 5h_resets_at, week, week_resets_at}."""
    best = {}  # window -> (ts, pct, resets_at)

    def offer(window, ts, pct, resets):
        ts = _parse_ts(ts)
        if ts is None or not isinstance(pct, (int, float)):
            return
        ts = _aware_utc(ts)
        if window not in best or ts > best[window][0]:
            best[window] = (ts, pct, resets)

    for rec in read():
        rl = rec.get("rate_limits")
        if rec.get("engine") == engine and rl:
            for w in ("5h", "week"):
                offer(w, rec.get("ts"), rl.get(w), rl.get(f"{w}_resets_at"))
    for rec in _jsonl(SHARED()):
        if (rec.get("kind") == "server" and rec.get("engine") == engine and not rec.get("model")
                and not str(rec.get("pool", "")).endswith(":unknown")):
            offer(rec.get("window"), rec.get("ts"), rec.get("used_pct"), rec.get("resets_at"))
    if not best:
        return None
    out = {}
    for w, (_, pct, resets) in best.items():
        out[w] = pct
        if resets is not None:
            out[f"{w}_resets_at"] = resets
    return out


def summary(records):
    by_engine = {}
    for rec in records:
        eng = rec.get("engine") or "?"
        s = by_engine.setdefault(
            eng,
            {
                "runs": 0,
                "seconds": 0,
                "input": 0,
                "cached": 0,
                "output": 0,
                "reasoning": 0,
                "latest_5h": None,
                "latest_week": None,
            },
        )
        s["runs"] += 1
        sec = rec.get("seconds")
        if isinstance(sec, (int, float)):
            s["seconds"] += sec
        usage = rec.get("usage") or {}
        u = _spend_usage(usage)
        # Codex input already includes its cached part; Claude reports cache reads and writes apart.
        s["input"] += u["input"] if eng == "codex" else u["input"] + u["cached"] + u["cache_creation"]
        s["cached"] += u["cached"]
        s["output"] += u["output"]
        if isinstance(usage.get("reasoning_output_tokens"), (int, float)):
            s["reasoning"] += usage["reasoning_output_tokens"]
        rl = rec.get("rate_limits")
        if rl:
            ts = _parse_ts(rec.get("ts"))
            if ts is not None:
                ts = _aware_utc(ts)
                prev = s.get("_latest_ts")
                if prev is None or ts > prev:
                    s["_latest_ts"] = ts
                    s["latest_5h"] = rl.get("5h")
                    s["latest_week"] = rl.get("week")
    for s in by_engine.values():
        s.pop("_latest_ts", None)
    return by_engine


def _fmt_tokens(n):
    n = int(n)
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}M" if v < 10 else f"{int(round(v))}M"
    if n >= 1000:
        v = n / 1000
        return f"{v:.1f}k" if v < 10 else f"{int(round(v))}k"
    return str(n)


def _fmt_seconds(total):
    total = int(round(total))
    if total < 60:
        return f"{total}s"
    mins = int(round(total / 60))
    if mins < 60:
        return f"{mins} min"
    hrs = mins / 60
    return f"{hrs:.1f} h" if hrs < 10 else f"{int(round(hrs))} h"


def _fmt_pct(val):
    if val is None:
        return None
    return f"{int(round(val))}%"


def _since_seconds(label):
    if label == "all":
        return None
    if label == "5h":
        return 5 * 3600
    if label == "week":
        return 7 * 24 * 3600
    if label == "today":
        now = datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int((now - midnight).total_seconds())
    raise ValueError(label)


def _format_line(engine, s):
    parts = [engine.ljust(7), f"{s['runs']} runs"]
    in_part = _fmt_tokens(s["input"]) + " in"
    if s["cached"]:
        in_part += f" ({_fmt_tokens(s['cached'])} cached)"
    parts.append(in_part)
    parts.append(_fmt_tokens(s["output"]) + " out")
    parts.append(_fmt_seconds(s["seconds"]))
    p5 = _fmt_pct(s.get("latest_5h"))
    pw = _fmt_pct(s.get("latest_week"))
    if p5 is not None:
        parts.append(f"5h {p5}")
    if pw is not None:
        parts.append(f"week {pw}")
    return "  ".join(parts)


def week_pct_per_run(records):
    """Codex week % one run adds, on average: the rise between consecutive readings of the
    same week (same reset time), over the runs. Other tools' use in between counts too."""
    runs = sorted((r for r in records if r.get("engine") == "codex" and r.get("rate_limits")),
                  key=lambda r: str(r.get("ts")))
    rise, prev = 0.0, None
    for r in runs:
        rl = r["rate_limits"]
        if prev and prev.get("week_resets_at") == rl.get("week_resets_at")                 and isinstance(rl.get("week"), (int, float)) and isinstance(prev.get("week"), (int, float)):
            rise += max(0.0, rl["week"] - prev["week"])
        prev = rl
    return round(rise / len(runs), 2) if len(runs) > 1 else None


def compare(records, at, last=30):
    """Per engine, the last `last` runs before `at` against every run since: did a change
    (a prompt rule, a budget rule) cut what one review costs?"""
    cut = _aware_utc(_parse_ts(at))
    timed = [(r, _parse_ts(r.get("ts"))) for r in records]
    before = [r for r, t in timed if t and _aware_utc(t) < cut]
    after = [r for r, t in timed if t and _aware_utc(t) >= cut]
    out = {}
    for label, rows in (("before", before), ("after", after)):
        for eng in {r.get("engine") or "?" for r in rows}:
            mine = [r for r in rows if (r.get("engine") or "?") == eng]
            mine = mine[-last:] if label == "before" else mine
            s, n = summary(mine)[eng], len(mine)
            out.setdefault(eng, {})[label] = {
                "runs": n, "avg_input": round(s["input"] / n), "avg_uncached": round((s["input"] - s["cached"]) / n),
                "avg_seconds": round(s["seconds"] / n), "week_pct_per_run": week_pct_per_run(mine)}
    return out


def measure_due():
    """~/.pr-gate/measure.json = {"since": ISO, "what": "...", "runs": 3} marks a change to measure.
    Once `runs` reviews ran after it, return the before/after once (the file is marked
    reported), so the agent running pr-gate shows the result without anyone remembering."""
    p = findings.home() / "measure.json"
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("reported") or _parse_ts(m.get("since")) is None:
        return None
    res = compare(read(), m["since"])
    if sum(e.get("after", {}).get("runs", 0) for e in res.values()) < int(m.get("runs", 3)):
        return None
    m["reported"] = _now_iso()
    p.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return {"what": m.get("what"), "since": m["since"], "compare": res}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", choices=("5h", "today", "week", "all"), default="today")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--compare", metavar="ISO_TIME", help="before/after a change, e.g. 2026-11-18T14:00Z")
    ap.add_argument("--last", type=int, default=30, help="with --compare: runs before the change to average")
    args = ap.parse_args(argv)
    if args.compare:
        if _parse_ts(args.compare) is None:
            ap.error("--compare needs an ISO time, e.g. 2026-11-18T14:00Z")
        res = compare(read(), args.compare, args.last)
        if args.as_json:
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0
        for eng in sorted(res):
            for label in ("before", "after"):
                c = res[eng].get(label)
                if not c:
                    print(f"{eng.ljust(7)} {label.ljust(6)}  no runs yet")
                    continue
                wk = "" if c["week_pct_per_run"] is None else f"  week +{c['week_pct_per_run']}%/run"
                print(f"{eng.ljust(7)} {label.ljust(6)}  {c['runs']} runs  {_fmt_tokens(c['avg_input'])} in/run"
                      f" ({_fmt_tokens(c['avg_uncached'])} uncached)  {_fmt_seconds(c['avg_seconds'])}/run{wk}")
        return 0
    records = read(_since_seconds(args.since))
    if not records:
        print("no reviews logged yet")
        return 0
    summ = summary(records)
    if args.as_json:
        print(json.dumps(summ, indent=2, sort_keys=True))
        return 0
    for engine in sorted(summ):
        print(_format_line(engine, summ[engine]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
