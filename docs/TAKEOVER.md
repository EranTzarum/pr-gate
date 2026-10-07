# pr-gate: takeover

For the session that continues pr-gate. Read this first, then `CLAUDE.md`, `SKILL.md`, and
`docs/evals.md` (the evidence for every rule).

## State (2026-10-07)

- **Repo:** public, `main` only, gate green (`py -3 -m unittest discover tests`, 61 tests).
  Installed for Claude Code through a junction in `~/.claude/skills/pr-gate`. Codex and Cursor get
  **copies** (Windows and WSL) in `~/.codex/skills/pr-gate` and `~/.cursor/skills/pr-gate`; re-sync
  them after every change (see *Releasing a change*).
- **Used live** on 5 PRs (`docs/evals.md`, runs 1–5):
  - a planted-bug eval PR on this repo;
  - an 18-file orchestrator PR (3 rounds and an escalation);
  - two PRs in a mobile-app repo that went all the way to merge. One of them the user ran
    themself with `/pr-gate`, about 15 minutes from start to merge.
- **What works end to end:**
  - waiting for CI as a background process;
  - context: diff, CI failure excerpts, artifacts, base-branch CI, docs, the repo's own merge
    rules, what a merge will trigger;
  - lean reviewer runs on codex/claude with mid-tier models;
  - verified findings with round history;
  - fix requests sent to the owner session as an app message (`send_message`), with a PR comment
    as the fallback;
  - the 3-round cap and escalation;
  - the green report;
  - a re-check, then a squash merge pinned to the head SHA;
  - a merge routed through the owner session when the repo's rules forbid outside merges.
- **Unattended runs:** none. Options and the recommendation are in `docs/automation.md`; enabling
  any of them is the user's decision.

## Open items, best first

1. **Engines not proven live:**
   - `subagent` has never run.
   - `cursor`: its CLI is slow (about 90 s of startup from the user's account plugins and MCPs).
     The empty-HOME lean sandbox hangs on Cursor CLI 2026.10.01, so `lean_run.py` runs cursor on
     the real profile. An orchestrator-side issue tracks the hang.
2. **Codex and Cursor as hosts are untested.** They have no `send_message`, so fixes go to PR
   comments. Smoke-test from a Codex session.
3. **Automatic stronger review for risky PRs.** Mid-tier codex at low effort missed an edge case in
   eval run 2. Detect migrations, auth/RLS, workflow and payment files in `pr_context.py`, and raise
   the effort or model for those PRs.
4. **`--reviewer both`** (codex and claude, findings merged) for high-risk PRs.
5. **PR-body owner marker.** A one-line rule for PR openers
   (`<!-- pr-gate:session=<id> host=claude-desktop -->`) would make owner routing exact. Today it
   matches `prNumber` + repo through `list_sessions`. Adding the rule to the user's global
   CLAUDE.md copies needs the user's OK.
6. **`lean_run.py` lives in this repo.** The umbrella rule says every headless agent run outside
   the orchestrator uses it. If a third consumer appears, move it to its own repo.
7. **`--all-open`** runs PRs one at a time. Fine until there are many open PRs.

## How to test a change

- **Unit:** `py -3 -m unittest discover tests`. There's no network; `pr_context.run` is mocked.
- **Context, live:** `py -3 scripts/pr_context.py <owner/repo> <N> --work %TEMP%\prg-live` on any
  real PR. Check `failed_logs`, `base_ci`, `behind_base`, `merge_rules` and `merge_triggers`.
- **Full loop:** a throwaway PR on this repo with a planted bug (eval run 1 pattern). Close it
  without merging and delete the branch.
- **Windows gotchas:**
  - PowerShell writes JSON with a BOM. `review.py` reads `utf-8-sig`, but piping into Python
    still breaks.
  - The Bash tool sometimes returns no output; use PowerShell then.
  - Heredocs containing `'` break the Bash wrapper; write scripts to a scratch file.

## Releasing a change

1. Run the gate (unit tests).
2. Run the public check, which must print no `LEAK:`:
   `node ~/.claude/skills/readmelyzer/scripts/check-readme.mjs . --public --all-files`.
3. Commit and push `main`.
4. Re-sync the copies:
   - **Windows:** `robocopy <repo> %USERPROFILE%\.codex\skills\pr-gate /MIR /XD .git __pycache__`,
     and the same for `.cursor`.
   - **WSL:** `rsync -a --delete --exclude .git --exclude __pycache__ <repo>/ ~/.codex/skills/pr-gate/`,
     and the same for `.cursor`.
5. Add a dated entry to `docs/evals.md` for anything learned from a live run.

## Boundaries

- **This session owns pr-gate only.** The umbrella's `CLAUDE.md`, `README.md` and `docs/` belong to
  the manager session (the one opened in the umbrella folder). Ask it, or the user, to update the
  pr-gate rows there after a big change.
- **Never merge in a repo whose rules forbid outside sessions from merging.** `merge_rules` finds
  those rules; the skill routes the merge to the owner session.
- **Old commits** still contain pre-anonymization text (private project names, no secrets; checked
  on 2026-10-07). Accepted, same as the other public skills.
