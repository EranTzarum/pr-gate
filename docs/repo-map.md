# Repo map

| Path | What |
|---|---|
| `SKILL.md` | The loop the host session follows |
| `INSTALL.md` | Install steps an agent follows when you say "install this skill" |
| `references/engines.md` | How to check, log in, test and pick a model for each reviewer CLI |
| `references/review-prompt.md` | The reviewer's instructions and JSON output format |
| `scripts/pr_context.py` | PR, diff, CI, logs, docs, merge triggers → one JSON file |
| `scripts/review.py` | Runs a read-only engine and gates its output |
| `scripts/lean_run.py` | Lean headless launcher for codex, claude and cursor (also usable on its own) |
| `scripts/findings.py` | Verifies findings, decides the verdict, keeps round state |
| `scripts/handoff.py` | Writes a fix request to `~/.pr-gate/inbox/` for the inbox mod |
| `mod/` | The inbox mod: delivers fix requests to the session whose branch has the PR |
| `scripts/wait.py` | Waits for CI or a new push, as a process rather than a polling model |
| `scripts/redact.py` | Secret masking |
| `CLAUDE.md`, `AGENTS.md` | Rules for working on this repo |

Back to the [README](../README.md).
