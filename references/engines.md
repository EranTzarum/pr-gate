# Reviewer engines: how to run each one from a terminal

For the agent running pr-gate. **You never need a plugin, an MCP server or an IDE integration
to use another model.** Every reviewer is a plain CLI that `scripts/lean_run.py` starts in a
read-only, lean run. Whatever host you are (Claude Code, Codex, Cursor), you call the same
scripts with `--engine <name>`. If a check below fails, tell the user the exact login command
and offer the next engine; do not stall and do not switch silently.

Python is `py -3` on Windows and `python3` elsewhere. `<skill>` is this folder.

## 1. Which engines are usable here

| Engine | Installed? | Logged in? | Log in (the user runs it; it opens a browser) |
|---|---|---|---|
| `codex` | `codex --version` | `codex login status` | `codex login` |
| `claude` | `claude --version` | `claude auth status` | `claude auth login` |
| `cursor` | `cursor-agent --version` | `cursor-agent status` | `cursor-agent login` |
| `subagent` | you are Claude Code with the Agent tool | n/a | n/a |

Use `cursor-agent`, never the bare `agent`: other CLIs (Grok Build) install their own `agent`
earlier on PATH, and it fails with Cursor's flags in a way that looks like a model error.

## 2. Prove an engine answers (one line, before a real review)

```bash
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py codex  --model gpt-6.1-sol --effort low --cwd .
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py claude --model sonnet --cwd .
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py cursor --model auto --cwd .
```

On PowerShell, write the prompt to a file and use `Get-Content prompt.txt | py -3 ...`.

Typical times: codex 20-40 s, claude about 7 s, cursor 50-170 s (its CLI loads the account's
plugins first). Exit 3 means it timed out and was killed; any other non-zero exit with an
"Authentication required" line means: ask the user to log in (table above).

## 3. Models

- **List what an account can use:** `cursor-agent models`; for codex, the model ids in
  `codex --help`/the user's plan; for claude, the aliases `sonnet`, `opus`, `haiku`.
- **Defaults** (mid tier): codex `gpt-6.1-sol` at low effort, claude `sonnet`, cursor
  `composer-2.5`. A high-risk PR raises codex to medium effort and claude to `opus`.
- **The user's own choice wins**, in this order: `--model`/`--effort` on the command, then
  `~/.pr-gate/config.json`, then the defaults above.

```json
{
  "reviewer": "cursor",
  "models": {"cursor": "auto", "codex": "gpt-6.1-sol"},
  "effort": {"codex": "low"},
  "risk_models": {"codex": "gpt-6.1-sol"}
}
```

`reviewer` is the engine used when none is given. `models`/`effort` replace the defaults per
engine. `risk_models` replaces the high-risk model; for an engine with no stronger tier
(cursor), a high-risk PR keeps the user's model. Create this file when the user states a
preference ("I want Cursor on auto"), then show it to them.

## 4. What lean_run actually runs

You never type these yourself; they are here so you can debug a failing engine.

| Engine | Command (read-only) |
|---|---|
| codex | `codex exec -m <model> --ignore-user-config --sandbox read-only -C <checkout> --json --skip-git-repo-check` with a login-only `CODEX_HOME`; on Windows also `-c windows.sandbox=unelevated` |
| claude | `claude -p --model <model> --output-format json --setting-sources project,local --strict-mcp-config --permission-mode plan --disable-slash-commands` |
| cursor | `cursor-agent -p --model <model> --trust --output-format json --workspace <checkout> --mode ask` |

Prompt and output go through files, never pipes (cursor hangs on pipes). A run that goes past
15 minutes is killed with its whole process tree.

## 5. Picking for the user

- Two or more engines: the *other* model from the host (Claude host -> codex, Codex host ->
  claude, Cursor host -> claude), unless `config.json` names a `reviewer`.
- One engine: that engine in a fresh run (Claude-only -> `subagent`).
- High-risk PR and both codex and claude work: offer `both`.
