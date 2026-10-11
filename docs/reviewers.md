# Reviewers and lean runs

Which engine reviews your PR, which model it runs, and why runs stay lean. Setup and login checks for each CLI: [references/engines.md](../references/engines.md).

## Pick a reviewer

| `--reviewer` | Default model | Note |
|---|---|---|
| `codex` | `gpt-6.1-sol`, low effort | Default when the host is Claude; also the default on Codex when Claude isn't installed |
| `claude` | `sonnet` | Default when the host is Codex or Cursor |
| `both` | codex and claude, findings merged | Offered for high-risk PRs; a cross-engine duplicate (same file, within 3 lines) is kept once, at the higher severity |
| `cursor` | `composer-2.5` | Opt-in when you have another tool; the default only on a Cursor-only setup. 45–110 s of startup, and it carries your Cursor account's plugins and MCP tools whatever the sandbox ([evals](evals.md)) |
| `subagent` | An Agent-tool subagent in the same session | Fresh context, same model family; the default on a Claude-only setup |

You pick the engine per run, and you can override the model with `--model` and `--effort`. If the engine fails, pr-gate tells you and offers the next one. It never switches engines silently.

**High-risk PRs get a stronger pass.** When the changed files touch migrations, auth/RLS, CI workflows or payments, codex runs at medium effort and claude runs `opus`, unless you pass `--model` or `--effort`. The reviewer is told which files triggered it, and the report says so.

## Lean runs

Every reviewer runs through `scripts/lean_run.py`. It's read-only, takes an explicit model, and loads none of your global MCP servers, skills, plugins or hooks, unless you grant one for that run. Same idea as the worker sandboxes multi-agent orchestrators use:

| Engine | What's left out | One-line prompt, before → after |
|---|---|---|
| codex | Everything in `~/.codex` except the login | timeout (>180 s) → 20 s |
| claude | User settings, hooks, plugins, skills, MCPs (the repo's own rules still apply) | 23 s → 7 s |
| cursor | Nothing: the CLI fetches the account's plugins from the server whatever `HOME` is; uses file I/O | 45–110 s either way |

The launcher also works on its own, from any terminal:

```bash
echo "Summarise src/ in 5 bullets" | py -3 "$HOME/.claude/skills/pr-gate/scripts/lean_run.py" codex --model gpt-6-luna --effort low --cwd .
cat prompt.txt | py -3 "$HOME/.claude/skills/pr-gate/scripts/lean_run.py" claude --model sonnet --mcp my-db --cwd .
```

Back to the [README](../README.md).
