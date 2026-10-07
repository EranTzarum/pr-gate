"""Run one read-only review round and gate it.

  py -3 review.py <context.json> --engine codex|claude|cursor [--model M] [--effort E]
  py -3 review.py <context.json> --engine subagent --prompt-only   # writes the prompt, prints its path
  py -3 review.py <context.json> --raw <reviewer-output-file>      # gate an existing output

Prints the findings.py result JSON: {summary, kept, dropped, verdict, round, action}.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import findings  # noqa: E402
import lean_run  # noqa: E402
from redact import redact  # noqa: E402

PROMPT = HERE.parent / "references" / "review-prompt.md"
DIFF_MAX_CHARS = 80000
TIMEOUT_S = 900  # a healthy review takes 1-3 min; 15 min means the engine is stuck
# Mid tier: enough to read a diff and confirm a file:line. Override with --model/--effort.
DEFAULT_MODELS = {"codex": ("gpt-6.1-sol", "low"), "claude": ("sonnet", None),
                  "cursor": ("composer-2.5", None)}
ENGINES = ("codex", "claude", "cursor", "subagent")


def build_prompt(ctx):
    diff = Path(ctx["diff_file"]).read_text(encoding="utf-8", errors="replace")
    if len(diff) > DIFF_MAX_CHARS:
        diff = diff[:DIFF_MAX_CHARS] + (f"\n\n[diff truncated at {DIFF_MAX_CHARS} chars; changed files: "
                                        + ", ".join(ctx["files"]) + ". Read them in the checkout.]")
    header = (f"{ctx['repo']} #{ctx['pr']}: {ctx['title']}\n"
              f"Branch {ctx['head']} -> {ctx['base']}, {ctx['size']['files']} files, "
              f"+{ctx['size']['additions']}/-{ctx['size']['deletions']}\n\nDescription:\n{ctx['body']}")
    docs = "\n".join(f"- {d}" for d in ctx["docs"]) or "- (none found; use the code's own conventions)"
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
    text = PROMPT.read_text(encoding="utf-8")
    for k, v in (("{{PR_HEADER}}", header), ("{{DOCS}}", docs), ("{{CI}}", ci), ("{{HISTORY}}", history),
                 ("{{DIFF}}", "```diff\n" + diff + "\n```")):
        text = text.replace(k, v)
    return text


def run_engine(engine, ctx, prompt, out, model=None, effort=None, timeout_s=None):
    """Read-only lean run (scripts/lean_run.py): explicit model, no global MCPs,
    skills or hooks."""
    model = model or DEFAULT_MODELS[engine][0]
    effort = effort or DEFAULT_MODELS[engine][1]
    code, stdout = lean_run.run(engine, model, prompt, Path(ctx["checkout"]), effort=effort,
                                timeout_s=timeout_s or TIMEOUT_S)
    out.write_text(redact(stdout), encoding="utf-8")
    if code == 3:
        raise RuntimeError(f"{engine} gave no answer in {(timeout_s or TIMEOUT_S) // 60} min; killed. Try another engine.")
    if code != 0 and not stdout.strip():
        raise RuntimeError(f"{engine} ({model}) exited {code} with no output")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("context")
    ap.add_argument("--engine", choices=ENGINES)
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--raw", help="gate an existing reviewer output instead of running an engine")
    ap.add_argument("--prompt-only", action="store_true")
    a = ap.parse_args(argv)
    ctx = json.loads(Path(a.context).read_text(encoding="utf-8-sig"))  # tolerate a BOM (PowerShell edits)
    stem = Path(a.context).with_suffix("")
    prompt = build_prompt(ctx)
    if a.prompt_only or a.engine == "subagent" and not a.raw:
        pf = stem.parent / (stem.name + ".prompt.md")
        pf.write_text(prompt, encoding="utf-8")
        print(json.dumps({"prompt_file": str(pf), "checkout": ctx["checkout"]}))
        return 0
    if a.raw:
        raw = Path(a.raw)
    else:
        if not a.engine:
            ap.error("--engine or --raw required")
        raw = stem.parent / f"{stem.name}.{ctx['head_sha'][:8]}.{a.engine}.out"
        try:
            run_engine(a.engine, ctx, prompt, raw, a.model, a.effort)
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(json.dumps({"error": str(e)[:800], "engine": a.engine}))
            return 3
    return findings.main(["check", "--raw", str(raw), "--root", ctx["checkout"], "--ci", ctx["ci"],
                          "--repo", ctx["repo"], "--pr", str(ctx["pr"]), "--sha", ctx["head_sha"]])


if __name__ == "__main__":
    sys.exit(main())
