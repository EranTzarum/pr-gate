# Reviewers and lean runs

Which engine reviews your PR, which model it runs, and why runs stay lean. Setup and login checks for each CLI: [references/engines.md](../references/engines.md).

## Pick a reviewer

| `--reviewer` | When | Note |
|---|---|---|
| `codex` | Default when the host is Claude; also on Codex when Claude isn't installed | Fast; spends your OpenAI weekly limit |
| `claude` | Default when the host is Codex or Cursor | |
| `both` | Offered for strong-tier PRs | codex and claude, findings merged; a cross-engine duplicate (same file, within 3 lines) is kept once, at the higher severity |
| `cursor` | Opt-in when you have another tool; the default only on a Cursor-only setup | About 160 s of fixed startup, 10–25 min per review; carries your Cursor account's plugins and MCP tools whatever the sandbox ([evals](evals.md)) |
| `subagent` | The default on a Claude-only setup | An Agent-tool subagent in the same session: fresh context, same model family |

You pick the engine per run. If the engine fails, pr-gate tells you and offers the next one. It never switches engines silently, except for the budget switch below, which the report names.

## The model follows the risk

Each PR gets a tier, and the tier picks the model:

| Engine | Light (docs/tests only) | Cheap (default) | Strong |
|---|---|---|---|
| codex | `gpt-6-luna`, low | `gpt-6.1-sol`, low | `gpt-6.1-sol`, medium |
| claude / subagent | `haiku` | `sonnet` | `opus` |
| cursor | `composer-2.5` | `composer-2.5` | `grok-4.7-high` |

**Strong** when any of these holds:
- the change score is 5 or more;
- the previous round found a high or blocker;
- this is the last round before the cap.

The score comes from what the added code does, not from its size:

| Signal | Points |
|---|---|
| Migration, auth/RLS, CI workflow or payment paths | +3 |
| Each kind of sensitive operation: permissions/secrets, running commands, guards, deletes, DB security | +2 |
| Locks/transactions | +1 |
| User isolation | +1 |
| A file that already had 2+ serious findings in this repo | +1 |
| Over 1,500 added code lines | +1 |

On 25 real PRs, every PR that turned out to have a high or blocker finding scored strong. Every report shows the tier line, e.g. `tier: strong (score 7: risk-path+3, exec/shell+2, concurrency+1)`.

**Overrides:**
- `--tier light|cheap|strong`, or `--model` / `--effort`.
- `~/.pr-gate/config.json` keys `tier_models` and `tier_threshold` ([engines guide](../references/engines.md)).

**Never used, and an error if you name them:**
- `gpt-6-astra` and `claude-fable-*`: cost.
- `gpt-6-terra`: refused on ChatGPT accounts.
- Cursor `auto`: billed from a metered pool.

Antigravity's `agy` runs through the launcher, but it is parked as a reviewer: it has no read-only mode that can still read files.

## What a review costs

- **Every review logs** its tokens, seconds and, for Codex, how much of your 5-hour and weekly limits is used, to `~/.pr-gate/usage.jsonl`.
- **See the totals** with `python3 scripts/usage.py --since today` (or `5h`, `week`, `all`).
- **Later rounds review only what changed** since the last reviewed commit, plus the earlier findings. The repo's docs go in once, capped.
- **Measured on a real PR:** Codex input in a later round dropped from 1.03M to 395k tokens (−61%) ([evals](evals.md)).
- **A `budget` in config** switches from Codex to another engine before you hit your limits. The cap of 3 rounds per PR is enforced; a 4th needs your OK (`--extra-round`).

## Lean runs

Every reviewer runs through `scripts/lean_run.py`. It's read-only, takes an explicit model, and loads none of your global MCP servers, skills, plugins or hooks, unless you grant one for that run. Same idea as the worker sandboxes multi-agent orchestrators use:

| Engine | What's left out | One-line prompt, before → after |
|---|---|---|
| codex | Everything in `~/.codex` except the login | timeout (>180 s) → 20 s |
| claude | User settings, hooks, plugins, skills, MCPs (the repo's own rules still apply) | 23 s → 7 s |
| cursor | Nothing: the CLI fetches the account's plugins from the server whatever `HOME` is; uses file I/O | about 160 s either way |

The launcher also works on its own, from any terminal:

```bash
echo "Summarise src/ in 5 bullets" | py -3 "$HOME/.claude/skills/pr-gate/scripts/lean_run.py" codex --model gpt-6-luna --effort low --cwd .
cat prompt.txt | py -3 "$HOME/.claude/skills/pr-gate/scripts/lean_run.py" claude --model sonnet --mcp my-db --cwd .
```

Back to the [README](../README.md).
