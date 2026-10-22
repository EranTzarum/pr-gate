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
| `agy` (parked as reviewer) | `agy --help` | (same binary; no separate status) | install per Antigravity docs |
| `subagent` | you are Claude Code with the Agent tool | n/a | n/a |

Use `cursor-agent`, never the bare `agent`: other CLIs (Grok Build) install their own `agent`
earlier on PATH, and it fails with Cursor's flags in a way that looks like a model error.

## 2. Prove an engine answers (one line, before a real review)

```bash
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py codex  --model gpt-6-luna --effort medium --cwd .
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py claude --model sonnet --cwd .
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py cursor --model composer-2.5 --cwd .
echo "Reply with exactly: ok" | python3 <skill>/scripts/lean_run.py agy    --model gemini-3.8-flash-low --cwd .
```

On PowerShell, write the prompt to a file and use `Get-Content prompt.txt | py -3 ...`.

For `agy`, `lean_run` turns the prompt into one NDJSON line on stdin (`stream-json`); you still
invoke it the same way. Read-only runs use `--mode plan` (not `--dangerously-skip-permissions`).
The agy test above is for other uses; agy is parked as reviewer (see below).

Typical times: codex 20-40 s, claude about 7 s, cursor about 160 s fixed startup per run
(measured 2026-10-12), agy about 48 s on a one-line "ok" test (~35k tokens fixed).
Exit 3 means it timed out and was killed; any other non-zero exit with an
"Authentication required" line means: ask the user to log in (table above).

## 3. Models and tiers

- **List what an account can use:** `cursor-agent models`; `agy models` (slugs differ per
  account; effort is in the slug, e.g. `gemini-3.8-flash-medium`); for codex, the model ids
  in `codex --help`/the user's plan; for claude, the aliases `sonnet`, `opus`, `haiku`.
- **Tiers:** `review.py` picks `light`, `cheap`, or `strong` from context and round history
  (`SKILL.md`). Defaults per engine and tier (`MODEL_TIERS`):

| Engine | light | cheap (default) | strong | critical |
|---|---|---|---|---|
| codex | `gpt-6-luna`, effort `medium` | `gpt-6-sol`, effort `medium` | `gpt-6.1-sol`, effort `medium` | `gpt-6.1-sol`, effort `high` |
| claude | `haiku`, effort `medium` | `sonnet`, effort `medium` | `opus`, effort `medium` | `opus`, effort `high` |
| cursor | `composer-2.5` | `composer-2.5` | `grok-4.7-high` | `grok-4.7-high` |
| agy (parked as reviewer) | `gemini-3.8-flash-medium` | `gemini-3.8-flash-medium` | `gemini-3.8-flash-high` | `claude-sonnet-5-5-medium` |

- Each engine climbs one model per tier: small, then mid, then top. Effort is part of every seat:
  a flag for codex and claude, and part of the model name for cursor and agy.
  - Codex's mid step is `gpt-6-sol`: `gpt-6-terra` is refused on ChatGPT accounts.
  - Cursor's `composer-2.5` has no effort variants.
- **Critical** = tier score at least `critical_threshold` (default 10), or the previous round
  found a blocker.
  - codex, claude and cursor keep their top model and raise its effort.
  - agy moves to its scarce Claude pool (below).
- **Pools and wallets.** Some tools bill two separate allowances. Stay on the generous one and use the
  scarce one only when it is worth it:

  | Tool | Generous pool | Scarce pool |
  |---|---|---|
  | Cursor | the included models (`composer-2.5`, `grok-*`) | the metered "Other Models" wallet, which runs out fast; `auto` can bill it, so `auto` is blocked |
  | agy | Gemini models | Claude and GPT models, a separate and much smaller allowance |

  For agy, every tier up to strong uses Gemini. Claude starts at critical, on Sonnet medium. Opus is
  never picked automatically.

  To climb further, set `tier_models.critical.agy` and move up one step at a time:
  1. `claude-sonnet-5-5-low`
  2. `claude-sonnet-5-5-medium`
  3. `claude-sonnet-5-5-high`
  4. `claude-opus-5-5-low`
  5. `claude-opus-5-5-medium`
  6. `claude-opus-5-5-high`

  Check what is left in the Antigravity app (model picker, "View Usage") before you move up.

- **Blocked** (refused from flags or config): `gpt-6-astra`, any `claude-fable-*`, `gpt-6-terra`,
  Cursor `auto`.
- **The user's own choice wins**, in this order: `--model`/`--effort` on the command, then
  `tier_models` for the active tier, then legacy `models`/`risk_models`, then the table above.

```json
{
  "reviewer": "cursor",
  "tier_threshold": 5,
  "tier_models": {
    "strong": {"claude": "opus"}
  },
  "models": {"cursor": "composer-2.5", "codex": "gpt-6-luna"},
  "effort": {"codex": "medium"},
  "risk_models": {"codex": "gpt-6.1-sol"},
  "budget": {
    "codex_5h_max_pct": 80,
    "codex_week_max_pct": 90,
    "fallback": "cursor"
  }
}
```

`tier_threshold` is the context tier score at which `review.py` chooses
`strong` (default 5). `reviewer` is the engine when none is given. `models`/`effort` still apply
to `light` and `cheap`; `risk_models` is a legacy alias for `strong` when `tier_models.strong`
does not name that engine. `budget` is optional: before a codex run, `review.py` compares the
latest logged codex `rate_limits` to these ceilings; at or over a set limit it runs `fallback`
(`claude` or `cursor`, default `cursor`) and sets `budget_switch` in the result. Omit a
key to ignore that window. Create this file when the user states a preference, then show it to
them.

**Cursor as reviewer:** plan on 10-25 minutes for a real PR (the CLI reads roughly one file
per step). Timeout is 25 minutes. **Never use `auto`:** Cursor bills Auto at the routed
model's list price from the metered "Other Models" pool; pick `composer-2.5` or a
`grok-4.7-*` slug from `cursor-agent models`. With `--output-format json`, the CLI prints
JSON only at the end, so an empty output file while the run is in progress is normal.

**Antigravity (`agy`) is parked as reviewer:** plan mode stops at the first shell command,
while sandbox mode can write, so it has no read-only mode that can still read files with commands.
`lean_run.py agy` still works for other read-only uses that do not need to read files with
commands; `review.py` rejects it as a reviewer or budget fallback.

## 4. What lean_run actually runs

You never type these yourself; they are here so you can debug a failing engine.

| Engine | Command (read-only) |
|---|---|
| codex | `codex exec -m <model> --ignore-user-config --sandbox read-only -C <checkout> --json --skip-git-repo-check` with a login-only `CODEX_HOME`; on Windows also `-c windows.sandbox=unelevated` |
| claude | `claude -p --model <model> --output-format json --setting-sources project,local --strict-mcp-config --permission-mode plan --disable-slash-commands` |
| cursor | `cursor-agent -p --model <model> --trust --output-format json --workspace <checkout> --mode ask` |
| agy (parked as reviewer) | `agy --model <model> --mode plan --add-dir <checkout> --disable-slash-commands --print= --input-format stream-json --output-format stream-json` (prompt as one NDJSON line on stdin; other uses only) |

Prompt and output go through files, never pipes (cursor hangs on pipes). Per-engine timeouts
(codex and claude 15 minutes, cursor 25 minutes, agy 20 minutes) kill the whole process tree.

## 5. Picking for the user

- Two or more engines: the *other* model from the host (Claude host -> codex, Codex host ->
  claude, Cursor host -> claude), unless `config.json` names a `reviewer`.
- One available reviewer engine: that engine in a fresh run (Claude-only -> `subagent`).
- High-risk PR and both codex and claude work: offer `both`.
