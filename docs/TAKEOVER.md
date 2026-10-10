# pr-gate: takeover

For the "pr-gate skill" session: opened in the umbrella folder, working only inside `pr-gate/`.
Read this first, then `CLAUDE.md`, `SKILL.md`, and `docs/evals.md` (the evidence for every rule).

## State (2026-10-10)

- **Repo:** public, `main` only, gate green (`py -3 -m unittest discover tests`, 72 tests; `claude
  plugin test mod`, 4 tests). Installed for Claude Code through a junction in
  `~/.claude/skills/pr-gate`, so **this folder must stay on `main`**: every session loads the skill
  from whatever is checked out here. Codex and Cursor get **copies** (Windows and WSL) in
  `~/.codex/skills/pr-gate` and `~/.cursor/skills/pr-gate`; re-sync them after every change.
- **Used live** on 6 PRs (`docs/evals.md`, runs 1–6), two of them merged in a mobile-app repo.
- **What works end to end:**
  - waiting for CI as a background process;
  - context: diff, CI failure excerpts, artifacts, base-branch CI, docs, merge rules, merge
    triggers, and `risk` (migrations, auth/RLS, workflows, payments);
  - reviewers: codex, claude, `both` (merged, cross-engine dedup), `subagent`; a high-risk PR
    auto-escalates (codex medium, claude `opus`);
  - verified findings with round history; the 3-round cap and escalation;
  - fix requests to the owner: app `send_message`, or `handoff.py` + the inbox mod (`mod/`) + a PR
    comment from any host;
  - the green report; a re-check, then a squash merge pinned to the head SHA; a merge routed
    through the owner session when the repo's rules forbid outside merges.
- **Unattended runs:** none. Options are in `docs/automation.md`; enabling any is the user's call.

## Decisions (2026-10-07, the user)

- The inbox mod is the hand-back channel for hosts without `send_message`, with a status line.
- `cursor` stays a reviewer, opt-in only (factory decision F4: it carries the account's plugins).
- No global rule for a PR-body owner marker: branch matching covers it; the marker stays optional.

## Open items, best first

1. **Cursor is logged out on this PC.** `cursor-agent login` (the user's step), then one timed
   `review.py --engine cursor` on any PR. It now launches `cursor-agent` (Grok's `agent` shadows
   the bare name).
2. **A Codex-hosted run has never happened.** `/pr-gate` from a Codex session on a throwaway PR:
   proves `handoff.py` + PR comment from a host with no `send_message`, and the inbox mod picking it
   up in a Claude owner session.
3. **The inbox mod is installed (2026-10-10)** as a skills-dir plugin: junction
   `~/.claude/skills/pr-gate-inbox` -> `pr-gate/mod` (open sessions: `/reload-plugins`), plus the
   user's `CLAUDE_CODE_PLUGIN_DIRS` (wins for new sessions; `claude plugin list` shows the other copy
   skipped, never both). Mods are early access: re-run `claude plugin validate mod` after a Claude Code update.
4. **`lean_run.py` lives in this repo.** Move it if a third consumer appears.
5. **`--all-open`** runs PRs one at a time. Fine until there are many open PRs.

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

- **This session owns pr-gate only.** The umbrella's `CLAUDE.md`, `README.md` and `docs/` belong to
  the manager session. Ask it, or the user, to update the pr-gate rows there after a big change.
- **Never merge in a repo whose rules forbid outside sessions from merging.** `merge_rules` finds
  those rules; the skill routes the merge to the owner session.
- **Old commits** still contain pre-anonymization text (no secrets; checked 2026-10-07). Accepted.
