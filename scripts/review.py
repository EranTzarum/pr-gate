"""Run one read-only review round and gate it.

  py -3 review.py <context.json> --engine codex|claude|cursor [--model M] [--effort E]
  py -3 review.py <context.json> --engine both [--effort E]        # codex + claude, findings merged
  py -3 review.py <context.json> --engine subagent --prompt-only   # writes the prompt, prints its path
  py -3 review.py <context.json> --raw <reviewer-output-file>      # gate an existing output

Prints the findings.py result JSON: {summary, kept, dropped, verdict, round, action},
plus `tier`, `escalated` (strong tier) and `partial` (with `both`, the engine
that failed).
"""
import argparse
import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import findings  # noqa: E402
import lean_run  # noqa: E402
import usage  # noqa: E402
from redact import redact  # noqa: E402

PROMPT = HERE.parent / "references" / "review-prompt.md"
DIFF_MAX_CHARS = 80000
MODEL_TIERS = {
    # Luna missed two mediums Sol found on a real PR (2026-10-13); cheap stays Sol-low until Luna is proven.
    "codex": {"light": ("gpt-6-luna", "low"), "cheap": ("gpt-6.1-sol", "low"),
              "strong": ("gpt-6.1-sol", "medium")},
    "claude": {"light": ("haiku", None), "cheap": ("sonnet", None), "strong": ("opus", None)},
    "cursor": {"light": ("composer-2.5", None), "cheap": ("composer-2.5", None),
               "strong": ("grok-4.7-high", None)},
    # agy bills two pools: Gemini (generous) and Claude/GPT (scarce, drains fast), like Cursor's
    # included bucket vs metered wallet. Gemini on every tier; Claude only at "critical", and
    # only Sonnet. Opus via agy is never picked automatically (--model or tier_models only).
    "agy": {"light": ("gemini-3.8-flash-low", None), "cheap": ("gemini-3.8-flash-medium", None),
            "strong": ("gemini-3.8-flash-high", None), "critical": ("claude-sonnet-5-5-medium", None)},
}
for _tiers in MODEL_TIERS.values():
    _tiers.setdefault("critical", _tiers["strong"])  # only agy has a separate critical seat
FORBIDDEN_MODELS = ("gpt-6-astra", "gpt-6-terra", "claude-fable", "auto")
TIER_THRESHOLD = 5
CRITICAL_THRESHOLD = 10  # score; a blocker in the previous round is critical too
DEFAULT_MODELS = {e: tiers["cheap"] for e, tiers in MODEL_TIERS.items()}
RISK_MODELS = {e: tiers["strong"] for e, tiers in MODEL_TIERS.items()}
ENGINES = ("codex", "claude", "cursor", "agy", "subagent", "both")
REVIEW_ENGINES_PARKED = ("agy",)
PARKED_REASON = ("agy is not available as a reviewer: it has no read-only mode that can still read files "
                 "(plan mode stops at the first command; sandbox mode can write). "
                 "Use codex, claude, cursor or subagent.")
BOTH = ("codex", "claude")


def build_prompt(ctx):
    delta_path = Path(ctx["delta_diff_file"]) if ctx.get("delta_diff_file") else None
    delta = delta_path.read_text(encoding="utf-8", errors="replace") if delta_path and delta_path.is_file() else ""
    diff = delta or Path(ctx["diff_file"]).read_text(encoding="utf-8", errors="replace")
    if len(diff) > DIFF_MAX_CHARS:
        diff = diff[:DIFF_MAX_CHARS] + (f"\n\n[diff truncated at {DIFF_MAX_CHARS} chars; changed files: "
                                        + ", ".join(ctx["files"]) + ". Read them in the checkout.]")
    diff = "```diff\n" + diff + "\n```"
    if delta:
        diff = (f"### Changes since the round you last reviewed ({ctx['last_reviewed_sha'][:8]}..{ctx['head_sha'][:8]})\n\n"
                + diff + "\n\n### All files this PR changes\n\n"
                + "\n".join(f"- {f}" for f in ctx["files"])
                + "\n\nCheck the earlier findings first, then review the changed lines; open other changed files only to confirm a finding.")
    header = (f"{ctx['repo']} #{ctx['pr']}: {ctx['title']}\n"
              f"Branch {ctx['head']} -> {ctx['base']}, {ctx['size']['files']} files, "
              f"+{ctx['size']['additions']}/-{ctx['size']['deletions']}\n\nDescription:\n{ctx['body']}")
    docs = "\n".join(f"- {d}" for d in ctx["docs"]) or "- (none found; use the code's own conventions)"
    digest = Path(ctx["docs_digest_file"]) if ctx.get("docs_digest_file") else None
    if digest and digest.is_file():
        docs = digest.read_text(encoding="utf-8", errors="replace")
    ci = {"pass": "CI passed.", "pending": "CI still running; judge the code only.",
          "none": "This repo has no CI on this PR. Look harder at what tests would have caught.",
          "fail": "CI FAILED. Failure excerpts (redacted):\n```\n" + ctx["failed_logs"] + "\n```"}[ctx["ci"]]
    base_runs = ctx.get("base_ci") or {}
    if ctx["ci"] == "fail" and base_runs:
        ci += "\nLatest run of each failing workflow on " + ctx["base"] + ": " + ", ".join(
            f"{wf} {r.get('conclusion')} @ {r.get('sha')}" for wf, r in base_runs.items())
        ci += (f". The PR is {ctx.get('behind_base')} commit(s) behind {ctx['base']}. If the base fails the same "
               "way, the PR did not cause it: say so in the finding's scenario.")
    earlier = [r for r in findings.load(ctx["repo"], ctx["pr"])["rounds"] if r["sha"] != ctx["head_sha"]]
    history = "\n".join(
        f"- round {n} @ {r['sha'][:8]}: " + ("; ".join(
            f"[{i['severity']}] {i['file']}:{i['line']} {i['title']}" for i in r.get("findings", [])) or "no findings")
        for n, r in enumerate(earlier, 1)) or "- none (first round)"
    rk = ctx.get("risk") or {}
    risk = ("High-risk PR. Touches: " + "; ".join(rk["why"]) + ". Read those files in full, not just the hunks."
            if rk.get("level") == "high" else "Nothing flagged by path; review as usual.")
    text = PROMPT.read_text(encoding="utf-8")
    for k, v in (("{{PR_HEADER}}", header), ("{{DOCS_TEXT}}", docs), ("{{CI}}", ci), ("{{HISTORY}}", history),
                 ("{{RISK}}", risk), ("{{DIFF}}", diff)):
        text = text.replace(k, v)
    return redact(text)


def run_engine(engine, ctx, prompt, out, model=None, effort=None, timeout_s=None, meta=None):
    """Read-only lean run (scripts/lean_run.py): explicit model, no global MCPs,
    skills or hooks."""
    if engine in REVIEW_ENGINES_PARKED:
        raise RuntimeError(PARKED_REASON)
    model = model or DEFAULT_MODELS[engine][0]
    effort = effort or DEFAULT_MODELS[engine][1]
    timeout_s = timeout_s if timeout_s is not None else lean_run.ENGINE_TIMEOUT_S[engine]
    meta = {} if meta is None else meta
    try:
        code, stdout = lean_run.run(engine, model, prompt, Path(ctx["checkout"]), effort=effort,
                                    timeout_s=timeout_s, meta=meta)
    finally:
        rounds = findings.load(ctx["repo"], ctx["pr"])["rounds"]
        usage.log(json.loads(redact(json.dumps(dict(meta, repo=ctx["repo"], pr=ctx["pr"],
                                                   sha=ctx["head_sha"], round=len(rounds) + 1)))))
    out.write_text(redact(stdout), encoding="utf-8")
    if code == 3:
        raise RuntimeError(f"{engine} gave no answer in {timeout_s // 60} min; killed. Try another engine.")
    if code != 0 and not stdout.strip():
        raise RuntimeError(f"{engine} ({model}) exited {code} with no output")
    return meta


def budget_engine(engine, cfg, ignore_budget, extra):
    budget = cfg.get("budget") or {}
    if engine != "codex" or ignore_budget or not budget:
        return engine
    limits = usage.last_limits("codex") or {}
    now = time.time()
    for window, key in (("5h", "codex_5h_max_pct"), ("week", "codex_week_max_pct")):
        resets = limits.get(f"{window}_resets_at")
        if isinstance(resets, (int, float)) and not isinstance(resets, bool) and now >= resets:
            continue
        val = limits.get(window)
        cap = budget.get(key)
        if val is not None and cap is not None and val >= cap:
            fallback = budget.get("fallback", "cursor")
            if fallback in REVIEW_ENGINES_PARKED:
                raise RuntimeError(PARKED_REASON)
            if fallback not in ("claude", "cursor"):
                raise RuntimeError("budget fallback must be claude or cursor")
            extra["budget_switch"] = {"from": "codex", "to": fallback,
                                      "5h": limits.get("5h"), "week": limits.get("week")}
            return fallback
    return engine


def load_config():
    """The user's preferences, ~/.pr-gate/config.json (or PR_GATE_HOME). All keys optional:
      {"reviewer": "cursor",                         default --engine
       "models": {"cursor": "composer-2.5"},         replaces cheap/light defaults
       "effort": {"codex": "low"},
       "risk_models": {"cursor": "grok-4.7-high"},    replaces strong defaults
       "tier_models": {"strong": {"claude": "opus"}},
       "tier_threshold": 5}
    A broken file is ignored (and said so), never fatal."""
    p = findings.home() / "config.json"
    if not p.exists():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
        return cfg if isinstance(cfg, dict) else {}
    except ValueError:
        print(json.dumps({"warning": f"{p} is not valid JSON; using defaults"}), file=sys.stderr)
        return {}


def tier_level(ctx, cfg, rounds):
    """Docs/tests use light; score and earlier review history can require strong."""
    tier = ctx.get("tier") or {}
    if tier.get("docs_only"):
        return "light", ["docs/tests only"]
    why = []
    if "tier" in ctx:
        threshold = cfg.get("tier_threshold", TIER_THRESHOLD)
        if tier.get("score", 0) >= threshold:
            why.extend(tier.get("why") or [f"tier score >= {threshold}"])
    elif (ctx.get("risk") or {}).get("level") == "high":
        why.extend(ctx["risk"].get("why") or ["high-risk PR"])
    earlier = [r for r in rounds if r["sha"] != ctx.get("head_sha")]
    last = earlier[-1].get("findings", []) if earlier else []
    if any(f.get("severity") in ("high", "blocker") for f in last):
        why.append("previous round found high/blocker")
    if len({r["sha"] for r in earlier}) == findings.MAX_ROUNDS - 1:
        why.append("last round before cap")
    critical = ("tier" in ctx and tier.get("score", 0) >= cfg.get("critical_threshold", CRITICAL_THRESHOLD)) \
        or any(f.get("severity") == "blocker" for f in last)
    if critical:
        return "critical", why
    return ("strong", why) if why else ("cheap", [])


def pick_model(engine, ctx, model=None, effort=None, cfg=None, level=None):
    """Flags > tier config > legacy config > tier defaults; blocked models always fail."""
    cfg = cfg or {}
    level = level or tier_level(ctx, cfg, [])[0]
    picked_model, picked_effort = MODEL_TIERS[engine][level]
    own = cfg.get("tier_models", {}).get(level, {}).get(engine)
    if own is None:
        own = cfg.get("risk_models" if level in ("strong", "critical") else "models", {}).get(engine)
    if isinstance(own, str):
        picked_model = own
    elif own is not None:
        picked_model, picked_effort = own
    if level not in ("strong", "critical"):
        picked_effort = cfg.get("effort", {}).get(engine, picked_effort)
    picked_model = model if model is not None else picked_model
    picked_effort = effort if effort is not None else picked_effort
    for forbidden in FORBIDDEN_MODELS:
        if (picked_model == forbidden if forbidden == "auto" else picked_model.startswith(forbidden)):
            reason = {"gpt-6-astra": "too expensive", "gpt-6-terra": "unavailable on a ChatGPT account",
                      "claude-fable": "too expensive", "auto": "bills a metered wallet"}[forbidden]
            raise RuntimeError(f"model {picked_model} is blocked: {reason}")
    return picked_model, picked_effort


def run_both(ctx, prompt, stem, model=None, effort=None, cfg=None, ignore_budget=False, extra=None, level=None):
    """codex and claude on the same prompt; findings tagged by engine and merged into one
    raw file (findings.verify drops cross-engine duplicates). One engine failing is
    reported as partial; both failing raises."""
    merged, summaries, failed = [], [], {}
    extra = {} if extra is None else extra
    used = set()
    for requested in BOTH:
        engine = budget_engine(requested, cfg or {}, ignore_budget, extra)
        if engine in used:
            continue
        used.add(engine)
        out = stem.parent / f"{stem.name}.{ctx['head_sha'][:8]}.{engine}.out"
        try:
            picked = pick_model(engine, ctx, model if engine == requested else None,
                                effort if engine == requested else None, cfg, level)
            run_engine(engine, ctx, prompt, out, *picked, meta=extra.setdefault("usage", {}).setdefault(engine, {}))
            got = findings.extract(out.read_text(encoding="utf-8", errors="replace"))
        except (RuntimeError, ValueError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            failed[engine] = str(e)[:400]
            continue
        summaries.append(got.get("summary", ""))
        merged += [dict(f, engine=engine) for f in got.get("findings", []) if isinstance(f, dict)]
    if len(failed) == len(used):
        raise RuntimeError("both engines failed: " + "; ".join(f"{k}: {v}" for k, v in failed.items()))
    raw = stem.parent / f"{stem.name}.{ctx['head_sha'][:8]}.both.out"
    raw.write_text(redact(json.dumps({"summary": max(summaries, key=len), "findings": merged})), encoding="utf-8")
    return raw, failed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("context")
    ap.add_argument("--engine", choices=ENGINES)
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--tier", choices=("light", "cheap", "strong", "critical"))
    ap.add_argument("--raw", help="gate an existing reviewer output instead of running an engine")
    ap.add_argument("--prompt-only", action="store_true")
    ap.add_argument("--ignore-budget", action="store_true", help="run codex despite configured usage limits")
    ap.add_argument("--extra-round", action="store_true", help="allow a new head beyond the round cap")
    a = ap.parse_args(argv)
    ctx = json.loads(Path(a.context).read_text(encoding="utf-8-sig"))  # tolerate a BOM (PowerShell edits)
    stem = Path(a.context).with_suffix("")
    rounds = findings.load(ctx["repo"], ctx["pr"])["rounds"]
    cfg = load_config()
    a.engine = a.engine or (None if a.raw else cfg.get("reviewer"))
    if a.engine and a.engine not in ENGINES:
        ap.error(f"config reviewer {a.engine!r} is not one of {', '.join(ENGINES)}")
    if a.engine in REVIEW_ENGINES_PARKED:
        print(json.dumps({"error": PARKED_REASON, "engine": a.engine}))
        return 3
    level, why = tier_level(ctx, cfg, rounds)
    if a.tier:
        level, why = a.tier, [f"--tier {a.tier}"]
    extra = {"tier": {"level": level, "score": (ctx.get("tier") or {}).get("score", 0), "why": why}}
    if level in ("strong", "critical"):
        extra["escalated"] = why
    if not a.raw and not a.extra_round:
        if len({r["sha"] for r in rounds if r["sha"] != ctx["head_sha"]}) >= findings.MAX_ROUNDS:
            print(redact(json.dumps(dict(extra,
                error=f"round cap reached ({findings.MAX_ROUNDS}); ask the owner, then re-run with --extra-round",
                repo=ctx["repo"], pr=ctx["pr"], sha=ctx["head_sha"]))))
            return 3
    prompt = build_prompt(ctx)
    if a.prompt_only or a.engine == "subagent" and not a.raw:
        pf = stem.parent / (stem.name + ".prompt.md")
        pf.write_text(prompt, encoding="utf-8")
        print(json.dumps(dict(extra, prompt_file=str(pf), checkout=ctx["checkout"])))
        return 0
    if a.raw:
        raw = Path(a.raw)
    else:
        if not a.engine:
            ap.error("--engine or --raw required")
        if a.engine == "both" and a.model:
            ap.error("--model names one engine's model; not with --engine both")
        try:
            if a.engine == "both":
                raw, failed = run_both(ctx, prompt, stem, a.model, a.effort, cfg, a.ignore_budget, extra, level)
                if failed:
                    extra["partial"] = failed
            else:
                engine = budget_engine(a.engine, cfg, a.ignore_budget, extra)
                raw = stem.parent / f"{stem.name}.{ctx['head_sha'][:8]}.{engine}.out"
                picked = pick_model(engine, ctx, a.model if engine == a.engine else None,
                                    a.effort if engine == a.engine else None, cfg, level)
                run_engine(engine, ctx, prompt, raw, *picked, meta=extra.setdefault("usage", {}).setdefault(engine, {}))
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(redact(json.dumps(dict(extra, error=str(e)[:800], engine=a.engine))))
            return 3
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = findings.main(["check", "--raw", str(raw), "--root", ctx["checkout"], "--ci", ctx["ci"],
                            "--repo", ctx["repo"], "--pr", str(ctx["pr"]), "--sha", ctx["head_sha"]])
    out = json.loads(buf.getvalue())
    if rc == 2:  # no findings JSON: show what the engine said (a login error reads as nothing otherwise)
        extra["engine_output"] = raw.read_text(encoding="utf-8", errors="replace").strip()[:400]
    print(redact(json.dumps(dict(out, **extra), indent=2)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
