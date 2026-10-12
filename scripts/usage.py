"""Append and summarize per-review usage from lean_run.

  py -3 usage.py [--since 5h|today|week|all] [--json]
"""
import argparse
import json
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


def log(record: dict) -> None:
    rec = dict(record)
    if "ts" not in rec:
        rec["ts"] = _now_iso()
    line = json.dumps(rec, separators=(",", ":")) + "\n"
    try:
        p = PATH()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        print(f"warning: usage log write failed: {exc}", file=sys.stderr)


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
    best = None
    best_ts = None
    for rec in read():
        if rec.get("engine") != engine:
            continue
        rl = rec.get("rate_limits")
        if not rl:
            continue
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            continue
        ts = _aware_utc(ts)
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best = rl
    return best


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
