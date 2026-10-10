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

So cursor runs on the real profile for now, with file I/O and an explicit model. This is tracked as an issue in the orchestrator repo.

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

## Live run 3: 2026-10-06, a real PR end to end (a private orchestrator repo)

**PR:** 18 files, +591/-43, owned by an agent session that the app had bound to the PR.

The first live test of the hand-back via `send_message`, of a red CI, and of escalation.

| Round | Head | CI | Codex `gpt-6.1-sol` medium | Kept | Action |
|---|---|---|---|---|---|
| 1 | `c558f1e4` | **fail**: windows-latest, 1 failure + 111 errors | 4 min, 30 commands, 0 blocked | 2 high, 1 medium | message delivered to the owner; it pushed |
| 2 | `4446907a` | pass | 2.4 min | 1 medium (a side effect of the round-1 fix) | message delivered; owner pushed |
| 3 | `ab7e9b6b` | pass | 2.5 min | 1 medium (narrow, Linux only) | **escalate** |

- I spot-checked both high findings and the round-2 medium in the code; all were real. None were dropped as unverifiable.
- Owner routing matched the session by `prNumber` alone. No PR-body marker was needed, because the app had bound the session to the PR.
- **Merge:**
  - The user replied `merge`. The head and CI were re-checked as unchanged, and the merge was sent to the owner session, because the repo's own rules (`CLAUDE.md` §0) forbid this session from merging.
  - The user then told the owner session directly to hold until morning. pr-gate stopped waiting.
  - The accepted medium became a follow-up issue in that repo.

**Gaps found, to fix:**
1. **The CI excerpt missed the error.** It took the last 120 lines of `--log-failed`, which were post-job cleanup. The real failure was in the test-output artifact (`gh run download`). Fix: cut the log around `##[error]`, `FAIL`, `ERROR` and `Traceback`, and pull small text artifacts from failed runs.
2. **No check of the repo's merge rules.** pr-gate should read the repo docs for a merge or read-only rule and route the merge to the owner session when this session may not merge.
3. **A BOM in the context JSON crashed `review.py`** (the file was written by PowerShell). It now reads with `utf-8-sig`.
4. **The `merge_triggers` notes are noisy:** on a mobile-app PR they also listed CI-only steps (`supabase db start` / `test db`).
5. **Duplicate docs** (`ARCHITECTURE.md` both at the root and in `docs/`) are both passed to the reviewer.
6. **No rule for draft PRs:** the report should say "mark it ready first" and never mark it ready itself.

## Live run 4: 2026-10-07, first real merge (a mobile-app repo: docs plus a dependency fix)

- **Round 1:** CI red. expo-doctor reported "8 packages out of date"; Expo had released patches since the base was last green, so the PR did not cause it. Codex took 72 s, found that and a real docs bug (a rule contradicting an invariant in DOMAIN_MODEL.md).
- **Decision:** the user chose to fix the dependencies in the same PR. The fix request went by message to the owner session, which pushed two commits.
- **Round 2:** CI green; Codex took 58 s and found nothing. Green report, then `merge`.
- **Merge:** re-checked, then squash with `--match-head-commit`. Merge commit `f36fa053`; only CI ran on main, no deploy (correct: `deploy.yml` has a `paths:` filter).
- **Total:** about 20 minutes, including the owner's fixes.

## Live run 5: 2026-10-07, the user ran `/pr-gate` themself (same app repo, a 1-file scheduled workflow)

The run went from start to merged in about 15 minutes (07:39 to 07:54 UTC):
- **Round 1:** CI red (the same stale-Expo failure). Codex took about 3 min; its only finding was that failure.
- **Fix:** after run 4's PR merged, the host noticed main already had the fix and merged main into the PR branch.
- **Round 2:** CI green, no findings.
- **Merge:** `merge` → re-check → squash with the head pinned → `54418d7c`.
- The reports were clear and in the skill's format.

**Problems seen:**
- **The host was also the PR's owner** (the session that wrote it), and the skill had no rule for that. It improvised and pushed to the PR branch, which the skill then forbade.
- **The same stale-CI case had to be reasoned out by hand.**

## Fixes after runs 3 to 5 (61 tests)

- **`pr_context.py`:**
  - Failure excerpts are cut around error lines, not the log tail, and small text artifacts of failed runs are included.
  - New fields:
    - `base_ci`: the latest run of each failing workflow on the base;
    - `behind_base`;
    - `merge_state`;
    - `merge_rules`: lines in the repo docs about who may merge or write.
  - Identical doc copies are read once.
  - `merge_triggers` applies `branches`/`paths`/`paths-ignore` filters and lists only deploy-like steps.
  - Fixed a regex bug that made every `push:` block look bare, so filters were never applied. This is why an earlier app PR was wrongly reported as deploying.
- **`review.py`:** reads the context with `utf-8-sig`, and tells the reviewer when the base fails the same way.
- **`SKILL.md`:**
  - *When you are the owner*: fix and push to the PR branch yourself; the review stays external.
  - Red CI the PR didn't cause: ask once; or update from the base if the base is already green.
  - Repos whose rules forbid this session from merging: send the pinned merge command to the owner session.
  - Drafts: never offer `merge`.
  - The green report gets a "Note" line.

## Live run 6: 2026-10-07 to 2026-10-10, risk escalation, `--engine both`, the inbox mod

**Setup:** throwaway PR #2 on this repo, "Add partial refund helper" in an `evals/billing/` path (closed without merging, branch deleted). Planted: **A** a refund can exceed the charge (high), **B** `to_cents` truncates float error, `"0.29"` -> 28 (medium), **C** an unused `MAX_REFUNDS` (low). No app session owned it; this session played the owner.

| Round | Head | CI | Engine | Time | Kept | Action |
|---|---|---|---|---|---|---|
| 1 | `8efcda88` | pass | `subagent` (first live use) | 33 s | A high, B high, C medium, plus a real low (tests only import from their own folder) | send-fix via the inbox |
| 2 | `c6b6cdd5` | pass | `both`: codex `gpt-6.1-sol` medium + claude `opus` (auto-escalated: `payment`) | 82 s together | none; both confirmed every round-1 fix; codex ran commands (23 KB of events) | green |
| 3 | same | | `cursor` | | not run: see below | |

**Worked:**
- `risk` flagged the PR (`payment: evals/billing/refund.py`) and `review.py` raised both engines without flags; the result carried `escalated`.
- `--engine both` merged two real answers into one round.
- The `subagent` engine: `--prompt-only`, one Agent call, `--raw` gate. All findings verified, none dropped.

**The inbox mod, and why it first did nothing:**
- Round 1's fix request sat in `~/.pr-gate/inbox/` for three days, untaken. Two causes:
  - it polled only when `session.start` said `isInteractive`, and a desktop-app session can start without a terminal;
  - it asked `gh pr view` in the session's cwd, but sessions here open in a parent folder and work in `<repo>/`.
- Fixed: it polls always and delivers only while a surface is attached (a `-p` run has none). `handoff.py` writes a `repo/pr/branch` header line. The mod matches the branch with local git in its cwd, or in a repo folder under its cwd that the session's own transcript shows it working in (so a manager session in the same parent folder does not take it).
- Re-sent, it arrived about a minute after the turn ended, as a new turn in the owner session ("The pr-gate-inbox plugin sent a message ..."). The owner fixed, pushed, and round 2 reviewed the new head. **First hand-back with no app `send_message`.**
- Mods API notes, measured: `prompt.submit` is refused inside a `command.run` hook (it would wait on the turn the hook holds), so `/pr-gate-inbox` schedules the delivery with `clock.after(0)`; `claude plugin validate` refuses a `$` passed to a function that is not declared at the top of the file; the engine hands fs hooks native absolute paths (`C:\h\inbox`).

**Found and fixed in pr-gate during the run:**
- `merge_rules` read a layout line ("a read-only engine ... a subagent's output") as a merge rule: "subagent" contains "agent". Word boundaries now, test added.
- **`agent` is not Cursor's any more.** Grok Build installs its own `agent.exe` earlier on PATH; the round-3 cursor review launched Grok with Cursor's flags and returned in 4 s with no JSON. `lean_run.py` now calls `cursor-agent`, the alias Cursor ships for this (the factory hit the same on 2026-10-08, its invariant 12). Test added.
- With the right binary, cursor answered in 16 s: `Authentication required`. The CLI is logged out on this PC; cursor stays unproven until `cursor-agent login`. `review.py` now shows the engine's first output lines when there is no findings JSON, so a login error is visible instead of "no findings JSON".

**Not exercised:** a Codex-hosted run (the inbox path from a host with no `send_message` at all), and cursor as a reviewer.
