"""Run one headless agent turn lean: explicit model, no global MCPs/skills/hooks
unless granted for this run. Same pattern as an orchestrator's worker sandboxes.

  py -3 lean_run.py codex  --model gpt-6.1-sol --effort low  --cwd DIR < prompt.txt
  py -3 lean_run.py claude --model sonnet --cwd DIR --mcp my-db < prompt.txt
  py -3 lean_run.py cursor --model composer-2.5 --cwd DIR < prompt.txt
  py -3 lean_run.py agy    --model gemini-3.8-flash-low --cwd DIR < prompt.txt

Read-only by default (--write lifts it). Prints the engine's stdout; exit 3 on
timeout (process tree killed), else the engine's exit code.

run(meta=dict) records wall seconds plus whatever usage the engine printed.

Measured 2026-10-04, one-line prompt:
  codex  real profile >180s (timeout)  ->  empty CODEX_HOME 20s
  claude real profile 23s              ->  user settings off 6.7s (--bare breaks login)
  cursor ~160s fixed startup, measured 2026-10-12, then the real work (earlier
         reads were 45-110s; the old "hang" was that startup past a short timeout).
         The CLI fetches the Cursor account's plugins and MCP servers from the
         server whatever HOME is, so a sandbox buys nothing: cursor runs on the
         real profile and carries those tools. Opt-in only.
  agy    startup ~48s, ~35k tokens fixed, read-only via --mode plan.
The global skills dir (~/.agents/skills) still leaks: HOME is not redirected.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ENGINES = ("codex", "claude", "cursor", "agy")
DEFAULT_TIMEOUT_S = 900
ENGINE_TIMEOUT_S = {"codex": 900, "claude": 900, "cursor": 1500, "agy": 1200}
# A lean CODEX_HOME holds only these.
CODEX_KEEP = ("auth.json", "installation_id")
WIN = sys.platform.startswith("win")


def _exe(name):
    return shutil.which(name) or name


def _wrap(argv):
    # npm/.cmd shims need cmd /c on Windows.
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
        if WIN:
            # --ignore-user-config also drops [windows] sandbox, and with none set codex
            # rejects EVERY command ("blocked by policy"): the reviewer reads only the
            # prompt. unelevated: commands run, 34 s. elevated: 98 s + git "dubious
            # ownership" (runs as a sandbox user). Measured 2026-10-04, codex-cli 0.159.2.
            argv += ["-c", "windows.sandbox=unelevated"]
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
        # `cursor-agent`, not `agent`: other CLIs (Grok's) also install an `agent` that can come first on PATH.
        argv = [_exe("cursor-agent"), "-p", "--model", model, "--trust", "--output-format", "json",
                "--workspace", str(cwd)] + ([] if write else ["--mode", "ask"])
    elif engine == "agy":
        if mcp or skills:
            raise RuntimeError("agy runs without grants")
        # effort is in the model slug. --mode plan is read-only; our timeout replaces --print-timeout.
        mode = ["--dangerously-skip-permissions"] if write else ["--mode", "plan"]
        argv = [_exe("agy"), "--model", model, *mode, "--add-dir", str(cwd),
                "--disable-slash-commands", "--print=",
                "--input-format", "stream-json", "--output-format", "stream-json"]
    else:
        raise ValueError(engine)
    return _wrap(argv), env


def kill_tree(p):
    if WIN:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.killpg(p.pid, signal.SIGKILL)


def _json_dicts(text):
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] != "{":
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _rate_limits(raw):
    if not isinstance(raw, dict):
        return None
    out = {}
    for src, pct, reset in (
        (raw.get("primary"), "5h", "5h_resets_at"),
        (raw.get("secondary"), "week", "week_resets_at"),
    ):
        if not isinstance(src, dict):
            continue
        if "used_percent" in src:
            out[pct] = src["used_percent"]
        if "resets_at" in src:
            out[reset] = src["resets_at"]
    return out or None


def _codex_meta(stdout, run_dir):
    usage = None
    for obj in _json_dicts(stdout):
        if obj.get("type") == "turn.completed" and isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
    fallback = limits = None
    for path in run_dir.glob("codex-home/sessions/**/rollout-*.jsonl"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for obj in _json_dicts(text):
            payload = obj.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                fallback = info["total_token_usage"]
            mapped = _rate_limits(payload.get("rate_limits"))
            if mapped:
                limits = mapped
    return usage if usage is not None else fallback, limits


def _agy_result(stdout):
    """Last stream-json result object, or None."""
    found = None
    for obj in _json_dicts(stdout):
        if obj.get("event") == "result" and isinstance(obj.get("result"), dict):
            found = obj["result"]
    return found


def _agy_stdout(code, stdout):
    """Callers see the review text. ERROR, empty answers and missing results become exit 2."""
    res = _agy_result(stdout)
    if res and res.get("status") == "SUCCESS":
        text = res.get("response")
        if isinstance(text, str) and text.strip():
            return code, text
        blocked = [a["action"] for a in res.get("denied_actions", []) if isinstance(a, dict) and a.get("action")]
        return 2, "agy gave no answer" + ("; blocked: " + ", ".join(blocked) if blocked else "")
    err = res.get("error") if res else None
    return 2, err if isinstance(err, str) and err else stdout


def _take(meta, text, keys):
    for obj in _json_dicts(text):
        for k in keys:
            if k in obj:
                meta[k] = obj[k]


def _fill_meta(meta, engine, model, effort, seconds, stdout, run_dir):
    meta["seconds"] = round(float(seconds), 1)
    meta["engine"] = engine
    meta["model"] = model
    meta["effort"] = effort
    if engine == "codex":
        usage, limits = _codex_meta(stdout, run_dir)
        if usage is not None:
            meta["usage"] = usage
        if limits:
            meta["rate_limits"] = limits
    elif engine == "cursor":
        _take(meta, stdout, ("duration_ms", "duration_api_ms", "usage"))
    elif engine == "claude":
        _take(meta, stdout, ("usage", "total_cost_usd", "duration_ms"))
    elif engine == "agy":
        res = _agy_result(stdout)
        if not res:
            return
        if isinstance(res.get("usage"), dict):
            meta["usage"] = res["usage"]
        secs = res.get("duration_seconds")
        if isinstance(secs, (int, float)):
            meta["duration_ms"] = secs * 1000
        if "status" in res:
            meta["status"] = res["status"]
        if "denied_actions" in res:
            meta["denied_actions"] = res["denied_actions"]


def run(engine, model, prompt, cwd, effort=None, write=False, mcp=(), skills=(),
        timeout_s=None, meta=None):
    """Run one turn. Returns (exit_code, stdout). exit 3 = timeout, tree killed.
    Prompt and output go through files: cursor's CLI hangs on pipes.
    timeout_s None uses ENGINE_TIMEOUT_S. meta, when a dict, is filled in place."""
    if not model:
        raise ValueError("model is required: lean runs never fall back to a CLI's default model")
    if timeout_s is None:
        timeout_s = ENGINE_TIMEOUT_S.get(engine, DEFAULT_TIMEOUT_S)
    base = Path(os.environ.get("LEAN_RUN_HOME") or Path.home() / ".lean-run")
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f"{engine}-", dir=base))
    try:
        argv, env = build(engine, model, cwd, run_dir, effort, write, mcp, skills)
        body = prompt
        if engine == "agy":
            # one NDJSON line; --input-format text rejects stdin as an empty prompt
            body = json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}) + "\n"
        (run_dir / "prompt.txt").write_text(body, encoding="utf-8")
        out_path = run_dir / "out.txt"
        with open(run_dir / "prompt.txt", "rb") as fin, open(out_path, "wb") as fout:
            t0 = time.monotonic()
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=fin, stdout=fout,
                                 stderr=subprocess.STDOUT, start_new_session=not WIN)
            try:
                code = p.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                kill_tree(p)
                p.wait(timeout=30)
                code = 3
        stdout = out_path.read_text(encoding="utf-8", errors="replace")
        if isinstance(meta, dict):
            try:
                _fill_meta(meta, engine, model, effort, time.monotonic() - t0, stdout, run_dir)
            except Exception:
                pass
        if engine == "agy" and code != 3:
            code, stdout = _agy_stdout(code, stdout)
        return code, stdout
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
    ap.add_argument("--timeout-min", type=int, default=None,
                    help="minutes (default: that engine's limit)")
    a = ap.parse_args(argv)
    timeout_s = None if a.timeout_min is None else a.timeout_min * 60
    try:
        code, out = run(a.engine, a.model, sys.stdin.read(), Path(a.cwd).resolve(), a.effort, a.write,
                        a.mcp, a.skill, timeout_s)
    except (RuntimeError, ValueError) as e:
        print(f"lean_run: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(out)
    if code == 3:
        mins = ENGINE_TIMEOUT_S.get(a.engine, DEFAULT_TIMEOUT_S) // 60 if a.timeout_min is None else a.timeout_min
        print(f"lean_run: no answer in {mins} min; process tree killed", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
