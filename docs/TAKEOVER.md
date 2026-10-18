# pr-gate: takeover

For the "pr-gate skill" session: opened in the umbrella folder, working only inside `pr-gate/`.
Read this first, then `AGENTS.md`, `SKILL.md`, and `docs/evals.md` (the evidence for every rule).

## State (2026-10-13) · `main` after the review-cost landing

- **Cost work (2026-10-12):** usage ledger (`scripts/usage.py`), codex budget switch, round cap
  with `--extra-round`, round-2+ delta diff and docs digest, Cursor 25 min timeout; docs in
  `SKILL.md`, `references/engines.md`, evals section below.
- **Tiers + agy (2026-10-13):** weighted `tier` score in `pr_context`, `light`/`cheap`/`strong`
  model picks in `review.py`, Antigravity (`agy`) as a fourth read-only engine; docs in
  `SKILL.md`, `references/engines.md`, evals "Reviewer tiers".
- **Repo:** public, `main` only. Gate green: `py -3 -m unittest discover tests` (133 tests),
  `claude plugin test mod` (4). Installed for Claude Code through a junction in
  `~/.claude/skills/pr-gate`, so **this folder must stay on `main`**: every session loads the skill
  from whatever is checked out here. Codex and Cursor get **copies** (Windows and WSL) in
  `~/.codex/skills/pr-gate` and `~/.cursor/skills/pr-gate`; re-sync them after every change (all
  synced at `13eba4a`).
- **Used live** on 9 PRs (`docs/evals.md`, runs 1-8 plus an orchestrator PR merged 2026-10-11 after
  the pattern-fix round). Best catch: a release-blocking workflow bug the writing session missed.
- **What works end to end:**
  - CI wait in the background; context with diff, CI excerpts, artifacts, base CI, docs, merge
    rules, merge triggers and `risk` (migrations, auth/RLS, workflows, payments);
  - reviewers: codex, claude, `both`, `subagent`, `cursor` (CLI `cursor-agent`), and `agy`
    (Antigravity); tier picks light/cheap/strong models; any single installed tool is enough (same
    tool, fresh run);
  - `~/.pr-gate/config.json`: default reviewer, model and effort per engine, high-risk model
    (the user's: cursor `composer-2.5` + a Codex budget); flags > config > defaults;
  - `references/engines.md`: per-CLI check, login, one-line test, models, exact command;
  - one finding per root cause; fix requests ask for the pattern fix (after a PR spent 3 rounds on
    siblings of one bug);
  - fix requests: app `send_message`, or `handoff.py` + inbox mod + PR comment; the mod delivered
    live once (run 6), matched by branch with local git;
  - green report, re-check, squash merge pinned to the head SHA; merge routed to the owner session
    when the repo's rules forbid outside merges.
- **README** is share-ready (readmelyzer pass, 5-step install per tool inside it; `INSTALL.md` for
  agents). **Unattended runs:** none (`docs/automation.md`; the user's call).

## Decisions (the user)

- 2026-10-07: inbox mod is the hand-back channel for hosts without `send_message`; cursor opt-in
  when another engine exists (factory F4); no global PR-body marker rule.
- 2026-10-11: works with any one tool; preferences in `~/.pr-gate/config.json`; codex stays the
  first choice over cursor (speed). Auto-merge for low-risk PRs: wanted later, not before more
  real runs. Vault: `2026-10-11 pr-gate works with any one tool and saves the reviewer choice`.

## Open items, best first

1. **Re-check tier threshold after 10 PRs** (`usage.py` totals and whether `strong` rounds missed
   blockers).
2. **Single-tool paths not proven live:** Codex-only and Cursor-only. The first friend on one is the
   test; ask for their run output and add it to `docs/evals.md`.
3. **Inbox mod in a real cross-session hand-back:** in every real run the gate session also owned
   the PR. Installed as the skills-dir plugin `~/.claude/skills/pr-gate-inbox` -> `pr-gate/mod`
   (open sessions: `/reload-plugins`), plus the user's `CLAUDE_CODE_PLUGIN_DIRS` (that copy wins in
   new sessions; never both). Re-run `claude plugin validate mod` after Claude Code updates.
4. **Watch the pattern rule:** on the next PR with several findings, a round-2 finding that is a
   sibling of a round-1 finding means the rule isn't working; tighten the prompt.
5. **Auto-merge for low-risk PRs:** the user wants it after 5-10 more real runs with no reviewer
   misses. Design it then (risk `normal`, CI green, no findings above low, the repo allows it).
6. **Docs-only PRs:** consider skipping the review or using the cheapest engine.
7. `lean_run.py` lives here (move if a third consumer appears); `--all-open` is sequential.
8. **Verify the cost cut live** (`usage.py` numbers before/after the context and budget changes).
9. **Shared with the factory (crew F15):**
   - **Done (2026-10-18):** `usage.py` appends each run to the machine-wide
     `~/.agent-usage/usage.jsonl` (contract: that folder's README). It writes one `spend` row,
     plus Codex `server` rows (5h, week). The Codex budget reads the newest pool-wide reading
     from any tool.
   - **Still open:** the shared seats file, so the two ladders can't drift. Also spending scarce
     allowance that would reset unused: Cursor and agy expose no usage today (agy only on a 429),
     so that waits for readings.

## How to test a change

- **Unit:** `py -3 -m unittest discover tests`. No network; `pr_context.run` is mocked.
- **Mod:** `claude plugin validate mod`, `claude plugin test mod`. To try a mod change live, copy
  it into the session's dev-mods folder (the `plugin-authoring` skill names it) and enable hot
  reloading.
- **Context, live:** `py -3 scripts/pr_context.py <owner/repo> <N> --work %TEMP%\prg-live`.
- **Full loop:** a throwaway PR on this repo with planted bugs (runs 1 and 6). Do it in a separate
  worktree or switch back to `main` right after: this folder is the installed skill. Close it
  without merging and delete the branch.
- **Windows gotchas:**
  - PowerShell pipes add a BOM: never pipe `review.py` output into Python; redirect to a file.
  - The Bash tool sometimes returns no output; use PowerShell then.
  - Heredocs containing `'` break the Bash wrapper; write scripts to a scratch file.
  - The public check reads an scp-style SSH remote (user, at-sign, host) as an email; use `https://` or `ssh://` URLs.

## Releasing a change

1. Run the gate (unit tests; mod tests if `mod/` changed).
2. Public check, no `LEAK:`: `node ~/.claude/skills/readmelyzer/scripts/check-readme.mjs . --public --all-files`.
3. Commit and push `main`.
4. Re-sync the copies:
   - **Windows:** `robocopy <repo> %USERPROFILE%\.codex\skills\pr-gate /MIR /XD .git __pycache__`,
     and the same for `.cursor`.
   - **WSL:** `rsync -a --delete --exclude .git --exclude __pycache__ <repo>/ ~/.codex/skills/pr-gate/`,
     and the same for `.cursor`.
5. Add a dated entry to `docs/evals.md` for anything learned from a live run.

## Boundaries

- **This session owns pr-gate only.** The umbrella's `AGENTS.md`, `README.md` and `docs/` belong to
  the manager session. Ask it, or the user, to update the pr-gate rows there after a big change.
- **Never merge in a repo whose rules forbid outside sessions from merging.** `merge_rules` finds
  those rules; the skill routes the merge to the owner session.
- **Old commits** still contain pre-anonymization text (no secrets; checked 2026-10-07). Accepted.
