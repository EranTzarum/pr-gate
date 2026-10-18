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
        for key, field in (
            ("input_tokens", "input"),
            ("cached_input_tokens", "cached"),
            ("output_tokens", "output"),
            ("reasoning_output_tokens", "reasoning"),
        ):
            val = usage.get(key)
            if isinstance(val, (int, float)):
                s[field] += val
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", choices=("5h", "today", "week", "all"), default="today")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
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
