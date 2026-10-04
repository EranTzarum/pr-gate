# pr-gate

Skill: drive a PR to a merge decision. CI wait, fresh-context read-only review,
fix requests to the owner session, 3-round cap, merge only on the user's `merge`.
The loop is in `SKILL.md`; the reviewer prompt is `references/review-prompt.md`.

## Layout

- `scripts/pr_context.py`: PR meta, redacted diff, CI state, failing-log tails, docs, merge triggers.
- `scripts/review.py`: builds the prompt, runs a read-only engine via `lean_run.py` or gates a subagent's output. Default models live in `DEFAULT_MODELS`.
- `scripts/lean_run.py`: lean headless launcher. Explicit model, no global MCPs/skills/hooks unless granted per run, file I/O, tree-kill on timeout.
- `scripts/findings.py`: extracts and verifies findings, the gate verdict, round state (`~/.pr-gate/state`).
- `scripts/wait.py`: blocks until CI settles or a new head SHA lands.
- `scripts/redact.py`: secret masking; every script's output passes through it.
- `docs/evals.md`: live runs. `docs/automation.md`: unattended options (none enabled).

## Rules

- Stdlib only. Tests use `unittest` and mock `pr_context.run`; no network in tests.
- Engines stay read-only unless `--write`: no `--force`, `--yolo`, `acceptEdits` or sandbox bypass in a default `lean_run.build` (a test enforces it).
- Never fall back to a CLI's default model, and never load the full profile silently: a missing login is an error.
- Nothing in this repo merges, pushes, edits repo settings or creates schedules.
- New secret patterns go into `redact.py` with a test.
- Fixtures are invented; never commit real PR diffs or CI logs.

## Gate

```bash
py -3 -m unittest discover tests
```
