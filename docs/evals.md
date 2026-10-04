# Evals

## Live run 1: 2026-10-04, throwaway PR on this repo

**Setup:** PR #1 "Add checkout discount helper" (an invented toy module, closed without merging, branch deleted). It had three planted problems:

- **A.** A cart total that skips the last item, plus a test that catches it, so CI goes red.
- **B.** A discount percent with no 0..100 bounds.
- **C.** An unused `MAX_ITEMS` constant (low).

No owner marker and no app session on the PR, so each fix request went out as a PR comment (the fallback path). This session played the owner.

| Round | Head | CI | Engine | Time / cost | Kept findings | Action |
|---|---|---|---|---|---|---|
| 1 | `5a630b7f` | fail (captured, redacted tail) | codex | 2m26s, 671 output tokens | high: A (correct line, cause matched the CI log) | send-fix, PR comment |
| 2 | `bcc8ed27` | pass | claude | 35s, $0.22, 1 turn | high: B; medium: negative qty (real, not planted); low: C | send-fix, PR comment |
| 3 | `a3a058f9` | pass | codex | 2m15s | medium: negative price via `apply_discount`; medium: C (re-graded from low) | **escalate** (cap 3) |

**Worked:**
- CI wait and failing-log capture.
- The reviewer tied the CI failure to the right line.
- Both engines parsed: codex JSONL and claude JSON.
- Zero findings dropped as unverifiable.
- The round cap fired on round 3.
- The fallback comment was posted.
- The PR was closed without merging.

**Found and fixed in pr-gate during the run:**
- `redact.py` masked token counts (`"input_tokens": 123`) because the key contains "token". Pure numbers are now never masked (test added).
- `list_sessions` has no branch field. Owner matching now uses `prNumber` + `cwd` remote.
- `wait.py ci` right after a push could see "no checks yet" and return `none`. Now `none` is accepted only after a 150 s grace (tests added).
- **Severity drift between engines:** C was low for claude and medium for codex, which forced a pointless round. Each round's prompt now lists the earlier rounds' findings with their severity and tells the reviewer to keep the grade unless the risk changed.

**Engine notes:**
- Codex missed B in round 1 and focused on the CI failure.
- Claude found all three plants plus one real extra.
- Consider `--reviewer claude` for the first round and codex for re-checks, or the reverse. That is the user's call per run.

**Cursor smoke test (same PR):**
- The `cursor` engine (`agent -p`) hung for 17 minutes with no output.
- Direct checks gave the same result every time: `--mode ask`, `--mode plan`, no mode, prompt on stdin or as an argument, `--model composer-2.5`. A one-line "reply ok" prompt timed out after 60–120 s in every variant, while `agent status` reported logged in.
- So it is a Cursor CLI problem on this machine (cursor-agent 2026.09.26), not pr-gate.
- It did expose a pr-gate bug. On a timeout, only `cmd` was killed, the node children kept stdout open, and the run could never return. `review.py` now kills the whole process tree, and the limit drops from 30 to 15 minutes (test added: a hung parent with a child dies in about 3 s).
- Treat `cursor` as **unverified** until a one-line `agent -p` answers again.

**Not exercised live:**
- `ask-merge` and the merge step. They are covered by unit tests only. The first real PR will exercise them.
- App `send_message` delivery (no session owned this PR).
- The `subagent` engine.

## Live run 2: 2026-10-04, lean launcher and mid-tier models

**Why:** A Cursor investigation showed that every headless launch loads the full profile:
- plugins, plugin hooks (security-guidance exits 127 on Windows after 22–27 s) and MCP servers, about 1 minute before the model is asked;
- defaults to the strongest model (Grok 4.7 High; about 13k input tokens for "reply ok").

Codex and Claude had the same problem. `scripts/lean_run.py` now runs every reviewer with an explicit model and nothing global unless it is granted for that run.

**One-line prompt ("Reply with exactly: ok"):**

| Engine | Full profile | Lean |
|---|---|---|
| codex `gpt-6.1-sol` low | timeout (>180 s) | 20 s (`CODEX_HOME` with the login only, plus `--ignore-user-config`) |
| claude `sonnet` | 23 s | 6.7 s (`--setting-sources project,local --disable-slash-commands --strict-mcp-config`) |
| claude `--bare` | | not logged in, so not used |
| cursor `composer-2.5` | pipes: timeout (>240 s); files: 91 s | empty-HOME sandbox: timeout (>150 s), even though `agent status` says logged in |

So cursor runs on the real profile for now, with file I/O and an explicit model. This is tracked in workflowai-factory#9.

**Same review as live run 1, round 3** (head `a3a058f9`):

| Engine | Before | Lean, mid tier | Kept findings |
|---|---|---|---|
| codex | 2m15s (default model) | **43 s** (`gpt-6.1-sol` low) | none |
| claude | 35 s | **20 s** (`sonnet`) | low: MAX_ITEMS (kept at low, as in round 2) |

**Trade-off:**
- At low effort, codex no longer flagged "negative price via apply_discount", which the stronger run graded medium.
- The mid tier is the default. For auth, migrations or payments PRs, run `--effort medium` or a stronger `--model`.

**Correction, same day: lean codex was blind.**
- `--ignore-user-config` also drops `[windows] sandbox = "elevated"` from the user config. With no Windows sandbox set, read-only codex rejects every command ("blocked by policy"), so the 43 s "green" above came from the diff in the prompt alone.
- Measured fix: `-c windows.sandbox=unelevated` (commands run, 34 s); `elevated` took 98 s and git refused with "dubious ownership".
- Re-run with the fix: 36 s, 4 commands, none blocked, green with no findings.
