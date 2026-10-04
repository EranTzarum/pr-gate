"""Run one read-only review round and gate it.

  py -3 review.py <context.json> --engine codex|claude|cursor [--model M]
  py -3 review.py <context.json> --engine subagent --prompt-only   # writes the prompt, prints its path
  py -3 review.py <context.json> --raw <reviewer-output-file>      # gate an existing output

Prints the findings.py result JSON: {summary, kept, dropped, verdict, round, action}.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import findings  # noqa: E402
from redact import redact  # noqa: E402

PROMPT = HERE.parent / "references" / "review-prompt.md"
DIFF_MAX_CHARS = 80000
TIMEOUT_S = 1800
CLAUDE_BUDGET_USD = "5"
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
          "fail": "CI FAILED. Failing job logs (tail, redacted):\n```\n" + ctx["failed_logs"] + "\n```"}[ctx["ci"]]
    text = PROMPT.read_text(encoding="utf-8")
    for k, v in (("{{PR_HEADER}}", header), ("{{DOCS}}", docs), ("{{CI}}", ci), ("{{DIFF}}", "```diff\n" + diff + "\n```")):
        text = text.replace(k, v)
    return text


def engine_argv(engine, checkout, model=None):
    """Read-only headless invocation per engine (flags as crew uses them)."""
    if engine == "codex":
        argv = ["cmd", "/c", shutil.which("codex") or "codex", "exec", "--sandbox", "read-only",
                "-C", checkout, "--json", "--skip-git-repo-check"]
    elif engine == "claude":
        argv = ["cmd", "/c", shutil.which("claude") or "claude", "-p", "--strict-mcp-config",
                "--permission-mode", "plan", "--output-format", "json",
                "--max-budget-usd", CLAUDE_BUDGET_USD]
    elif engine == "cursor":
        argv = ["cmd", "/c", shutil.which("agent") or "agent", "-p", "--mode", "ask", "--trust",
                "--output-format", "json", "--workspace", checkout]
    else:
        raise ValueError(engine)
    if model:
        argv += ["--model", model] if engine != "codex" else ["-m", model]
    if not sys.platform.startswith("win"):
        argv = argv[2:]
    return argv


def run_engine(engine, ctx, prompt, out, model=None):
    p = subprocess.run(engine_argv(engine, ctx["checkout"], model), input=prompt, cwd=ctx["checkout"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=TIMEOUT_S)
    out.write_text(redact(p.stdout), encoding="utf-8")
    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError(f"{engine} exited {p.returncode}: {redact(p.stderr.strip())[-600:]}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("context")
    ap.add_argument("--engine", choices=ENGINES)
    ap.add_argument("--model")
    ap.add_argument("--raw", help="gate an existing reviewer output instead of running an engine")
    ap.add_argument("--prompt-only", action="store_true")
    a = ap.parse_args(argv)
    ctx = json.loads(Path(a.context).read_text(encoding="utf-8"))
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
            run_engine(a.engine, ctx, prompt, raw, a.model)
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(json.dumps({"error": str(e)[:800], "engine": a.engine}))
            return 3
    return findings.main(["check", "--raw", str(raw), "--root", ctx["checkout"], "--ci", ctx["ci"],
                          "--repo", ctx["repo"], "--pr", str(ctx["pr"]), "--sha", ctx["head_sha"]])


if __name__ == "__main__":
    sys.exit(main())
