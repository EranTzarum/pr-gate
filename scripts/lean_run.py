"""Run one headless agent turn lean: explicit model, no global MCPs/skills/hooks
unless granted for this run. Pattern taken from crew's measured worker sandboxes.

  py -3 lean_run.py codex  --model gpt-6.1-sol --effort low  --cwd DIR < prompt.txt
  py -3 lean_run.py claude --model sonnet --cwd DIR --mcp supabase-brofix < prompt.txt
  py -3 lean_run.py cursor --model composer-2.5 --cwd DIR < prompt.txt

Read-only by default (--write lifts it). Prints the engine's stdout; exit 3 on
timeout (process tree killed), else the engine's exit code.

Measured 2026-10-04, one-line prompt:
  codex  real profile >180s (timeout)  ->  empty CODEX_HOME 20s
  claude real profile 23s              ->  user settings off 6.7s (--bare breaks login)
  cursor pipes hang; file I/O 91s. Its empty-HOME sandbox hangs on CLI 2026.10.01,
         so cursor still runs on the real profile (ponytail: switch back when fixed).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINES = ("codex", "claude", "cursor")
DEFAULT_TIMEOUT_S = 900
# A worker's CODEX_HOME may hold only these (crew CODEX_SANDBOX_KEEP).
CODEX_KEEP = ("auth.json", "installation_id")
WIN = sys.platform.startswith("win")


def _exe(name):
    return shutil.which(name) or name


def _wrap(argv):
    # npm/.cmd shims need cmd /c on Windows (as crew does).
    return ["cmd", "/c"] + argv if WIN else argv


def codex_home(run_dir, skills=()):
    real = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    home = run_dir / "codex-home"
    home.mkdir()
    if not (real / "auth.json").is_file():
        raise RuntimeError(f"no codex login at {real / 'auth.json'}; refusing to run on the full profile")
    for name in CODEX_KEEP:
        if (real / name).is_file():
            shutil.copy2(real / name, home / name)  # copy, never link: codex rewrites it on refresh
    for s in skills:
        src = real / "skills" / s
        if not src.is_dir():
            raise RuntimeError(f"codex skill not found: {s}")
        shutil.copytree(src, home / "skills" / s)
    return home


def toml_value(v):
    """Python value -> TOML literal for `codex -c key=value`."""
    if isinstance(v, dict):
        return "{" + ", ".join(
            f"{k if k.replace('_', '').replace('-', '').isalnum() else json.dumps(k)} = {toml_value(x)}"
            for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(v)  # strings, numbers, true/false read the same in TOML


def codex_mcp_overrides(names):
    if not names:
        return []
    import tomllib
    real = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    servers = tomllib.loads(real.read_text(encoding="utf-8")).get("mcp_servers", {})
    out = []
    for n in names:
        if n not in servers:
            raise RuntimeError(f"codex MCP not in config.toml: {n}")
        for k, v in servers[n].items():
            out += ["-c", f"mcp_servers.{n}.{k}={toml_value(v)}"]
    return out


def claude_mcp_config(run_dir, names):
    src = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8")).get("mcpServers", {})
    missing = [n for n in names if n not in src]
    if missing:
        raise RuntimeError(f"claude MCP not in ~/.claude.json: {', '.join(missing)}")
    path = run_dir / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {n: src[n] for n in names}}), encoding="utf-8")
    return path


def build(engine, model, cwd, run_dir, effort=None, write=False, mcp=(), skills=()):
    """(argv, env) for one lean run."""
    env = dict(os.environ)
    if engine == "codex":
        env["CODEX_HOME"] = str(codex_home(run_dir, skills))
        argv = [_exe("codex"), "exec", "-m", model, "--ignore-user-config",
                "--sandbox", "workspace-write" if write else "read-only",
                "-C", str(cwd), "--json", "--skip-git-repo-check"] + codex_mcp_overrides(mcp)
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
    elif engine == "claude":
        argv = [_exe("claude"), "-p", "--model", model, "--output-format", "json",
                "--setting-sources", "project,local",  # no user hooks/plugins; repo's own rules still apply
                "--strict-mcp-config",
                "--permission-mode", "acceptEdits" if write else "plan"]
        if not skills:
            argv.append("--disable-slash-commands")
        # ponytail: a skill grant turns all skills back on; claude has no per-skill switch here.
        if mcp:
            argv += ["--mcp-config", str(claude_mcp_config(run_dir, mcp))]
        if effort:
            argv += ["--effort", effort]
    elif engine == "cursor":
        if mcp or skills:
            raise RuntimeError("cursor runs on the real profile for now; grants are not isolated")
        argv = [_exe("agent"), "-p", "--model", model, "--trust", "--output-format", "json",
                "--workspace", str(cwd)] + ([] if write else ["--mode", "ask"])
    else:
        raise ValueError(engine)
    return _wrap(argv), env


def kill_tree(p):
    if WIN:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.killpg(p.pid, signal.SIGKILL)


def run(engine, model, prompt, cwd, effort=None, write=False, mcp=(), skills=(), timeout_s=DEFAULT_TIMEOUT_S):
    """Run one turn. Returns (exit_code, stdout). exit 3 = timeout, tree killed.
    Prompt and output go through files: cursor's CLI hangs on pipes."""
    if not model:
        raise ValueError("model is required: lean runs never fall back to a CLI's default model")
    base = Path(os.environ.get("LEAN_RUN_HOME") or Path.home() / ".lean-run")
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f"{engine}-", dir=base))
    try:
        argv, env = build(engine, model, cwd, run_dir, effort, write, mcp, skills)
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        out_path = run_dir / "out.txt"
        with open(run_dir / "prompt.txt", "rb") as fin, open(out_path, "wb") as fout:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=fin, stdout=fout,
                                 stderr=subprocess.STDOUT, start_new_session=not WIN)
            try:
                code = p.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                kill_tree(p)
                p.wait(timeout=30)
                code = 3
        return code, out_path.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)  # holds a credential copy: never leave it behind


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("engine", choices=ENGINES)
    ap.add_argument("--model", required=True)
    ap.add_argument("--effort", help="reasoning effort (codex: low|medium|high…, claude: low|medium|high)")
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--write", action="store_true", help="allow edits (default read-only)")
    ap.add_argument("--mcp", action="append", default=[], help="grant one MCP server for this run (repeatable)")
    ap.add_argument("--skill", action="append", default=[], help="grant one skill for this run (repeatable)")
    ap.add_argument("--timeout-min", type=int, default=DEFAULT_TIMEOUT_S // 60)
    a = ap.parse_args(argv)
    try:
        code, out = run(a.engine, a.model, sys.stdin.read(), Path(a.cwd).resolve(), a.effort, a.write,
                        a.mcp, a.skill, a.timeout_min * 60)
    except (RuntimeError, ValueError) as e:
        print(f"lean_run: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(out)
    if code == 3:
        print(f"lean_run: no answer in {a.timeout_min} min; process tree killed", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
