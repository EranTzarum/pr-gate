# pr-gate

Skill: drive a PR to a merge decision. CI wait, fresh-context read-only review,
fix requests to the owner session, 3-round cap, merge only on the user's `merge`.
The loop is in `SKILL.md`; the reviewer prompt is `references/review-prompt.md`.

## Layout

- `scripts/pr_context.py`: PR meta, redacted diff, CI state, failing-log tails, docs, merge triggers.
- `scripts/review.py`: builds the prompt, runs a read-only engine (codex/claude/cursor) or gates a subagent's output.
- `scripts/findings.py`: extracts and verifies findings, the gate verdict, round state (`~/.pr-gate/state`).
- `scripts/wait.py`: blocks until CI settles or a new head SHA lands.
- `scripts/redact.py`: secret masking; every script's output passes through it.
- `docs/evals.md`: live runs. `docs/automation.md`: unattended options (none enabled).

## Rules

- Stdlib only. Tests use `unittest` and mock `pr_context.run`; no network in tests.
- Engines stay read-only: no `--force`, `--yolo`, `acceptEdits` or sandbox bypass in `engine_argv` (a test enforces it).
- Nothing in this repo merges, pushes, edits repo settings or creates schedules.
- New secret patterns go into `redact.py` with a test.
- Fixtures are invented; never commit real PR diffs or CI logs.

## Gate

```bash
py -3 -m unittest discover tests
```
