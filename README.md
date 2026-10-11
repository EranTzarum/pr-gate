<div align="center">

<img src="docs/assets/logo.svg" alt="pr-gate logo" width="112">

# pr-gate

**Merge on a reply, not on a read.**
A skill that reviews each pull request, sends the fixes back to the session that wrote it, and asks you only one thing at the end: `merge`?

[![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Cursor-16A34A)](#install)
[![Reviewers](https://img.shields.io/badge/reviewers-codex%20%C2%B7%20claude%20%C2%B7%20cursor-052E16)](#pick-a-reviewer)
[![Tests](https://img.shields.io/badge/tests-74%20passing-16A34A)](#install)
[![License: MIT](https://img.shields.io/badge/license-MIT-052E16)](LICENSE)

</div>

---

## At a glance

| You do | pr-gate does | You get |
|---|---|---|
| `/pr-gate owner/repo 12` | Waits for CI. A reviewer that didn't write the code checks it against the repo's own docs. Fix requests go to the owner session, for up to 3 rounds | A short, plain report and one question: reply `merge` |

<p align="center">
  <img src="docs/assets/loop.svg" alt="New PR or push, wait for CI, fresh review, fix request to the owner session for up to 3 rounds, then a green report and your merge reply" width="980">
</p>

---

## Why

The agent that wrote a change is the worst judge of it. A second model, with a fresh context, keeps finding real defects. Green CI is not enough either: local checks can pass while the deploy breaks.

Doing this by hand means reading diffs, pasting findings into the right chat, and checking back after every push. pr-gate runs that loop and stops only when it needs you:

| It stops for you when | You see |
|---|---|
| CI is green and only low findings remain | What the PR does, what was checked, what merging will trigger |
| 3 rounds pass and it still isn't clean | What was fixed, what's still open, your options |
| No session owns the branch | The fix request posted on the PR, and which session should pick it up |

---

## What it did in practice

Measured on its first eight real pull requests (details in [docs/evals.md](docs/evals.md)):

- **It catches what the writer missed.** On a 28-file PR, the session that wrote it had already reviewed its own branch. pr-gate's reviewer still found a blocker: the release workflow would have refused every future tagged release. It was fixed before merge.
- **It's fast.** A round (fresh review of the diff, the repo's docs and the CI logs) takes 1 to 5 minutes. A two-round PR goes from open to merged in about 15 to 20 minutes, and most of that is CI.
- **You stop being the messenger.** No more copying findings between chats or checking back after each push. You read one short report and answer one word.
- **It costs little of your main quota.** Reviewers run lean (mid-tier model, none of your global plugins or MCP servers). With Claude as your editor and Codex as the reviewer, the review comes out of a different subscription.

---

## Install

Pick the tool you use. Any one of them is enough: with a single tool, the reviewer is that same tool in a fresh, read-only run. A fresh context is what catches the writer's blind spots; a second model family is a bonus.

**Needs:** [`gh`](https://cli.github.com) logged in (`gh auth login`), Python 3, `git`.

**Easiest:** paste this repo's link into your agent and say *"install this skill"*. It follows [INSTALL.md](INSTALL.md).

Or by hand, in five steps:

**1. Get the skill** into the folder of the tool you use:

| You use | Run once |
|---|---|
| Claude Code | `git clone https://github.com/EranTzarum/pr-gate.git ~/.claude/skills/pr-gate` |
| Codex | `git clone https://github.com/EranTzarum/pr-gate.git ~/.codex/skills/pr-gate` |
| Cursor | `git clone https://github.com/EranTzarum/pr-gate.git ~/.cursor/skills/pr-gate` |

On Windows, `~` is `%USERPROFILE%`. Using two of these tools? Clone it once and copy (or link) the folder into the others.

**2. Check it:** inside the skill folder, `python3 -m unittest discover tests` (`py -3` on Windows). Expected: `Ran 74 tests ... OK`. No network calls; `gh` is mocked.

**3. Make sure your reviewer can answer.** Every reviewer is a plain CLI, so no plugin is needed to use a model from another tool. For each one you have:

| Reviewer | Logged in? | If not, log in | Quick test (expects `ok`) |
|---|---|---|---|
| Codex | `codex login status` | `codex login` | `echo "Reply with exactly: ok" \| python3 ~/.claude/skills/pr-gate/scripts/lean_run.py codex --model gpt-6.1-sol --cwd .` |
| Claude | `claude auth status` | `claude auth login` | same, with `claude --model sonnet` |
| Cursor | `cursor-agent status` | `cursor-agent login` | same, with `cursor --model auto` (slow to start: up to ~3 min) |

Use the skill folder you cloned into in the test command. Use `cursor-agent`, not `agent`: other tools install an `agent` command too. More (models, what each one runs): [references/engines.md](references/engines.md).

**4. Optional: save your preference** in `~/.pr-gate/config.json`, or just tell the agent *"review with Cursor on the auto model"* and it writes it:

```json
{ "reviewer": "cursor", "models": { "cursor": "auto" } }
```

**5. Run it** in a session inside your repo:

```text
/pr-gate owner/repo 12
```

In Codex or Cursor, say *"run pr-gate on PR 12"* if slash commands don't list skills. Without a saved preference it asks once which reviewer to use, offering the best one you have.
### Optional: the inbox mod (Claude Code only)

You don't need it. Without it, fix requests reach the session that owns the PR as an app message (Claude desktop app) or as a PR comment, and the loop works the same.

It helps when you run pr-gate from one tool and write code in Claude Code: the fix request then arrives in the writing session by itself. `mod/` is a Claude Code mod (function hooks that run inside a session):
- every minute it looks in `~/.pr-gate/inbox/`, with local `git` only;
- a request goes to the session that has the PR's branch checked out, in its folder or in a repo folder under it that it works in;
- that session gets a new turn with the request once it's idle, and its status line shows the PR's round.

`/pr-gate-inbox` checks right away. Headless runs (`claude -p`) never pick anything up.

```bash
# every session; open ones pick it up after /reload-plugins
ln -s ~/.claude/skills/pr-gate/mod ~/.claude/skills/pr-gate-inbox
```

On Windows: `cmd /c mklink /J "%USERPROFILE%\.claude\skills\pr-gate-inbox" "%USERPROFILE%\.claude\skills\pr-gate\mod"`. For one session only, use `claude --plugin-dir ~/.claude/skills/pr-gate/mod`. The mod API is early access; `claude plugin test mod` checks it (`4 pass`).

---

## Example

A PR adds a checkout discount helper. CI is red. Three rounds later:

```text
round 1  CI fail   codex   [high]   cart.py:13  cart total skips the last item (matches the CI log)
         → fix request sent, owner pushes
round 2  CI pass   claude  [high]   cart.py:8   percent not bounded: 150% gives a refund
                           [medium] cart.py:14  negative qty cancels other items
         → fix request sent, owner pushes
round 3  CI pass   codex   2 × medium remain
         → escalate: "3 rounds, still 2 medium. Fixed so far: … Options: …"
```

When a round comes back clean, you get this instead:

```text
PR owner/repo#12 "Add discount helper" is ready. Reply `merge` to merge it.
What it does: adds bounded percentage discounts and cart totals.
Checked: CI pass; claude review over 3 files, 2 rounds; repo docs: CLAUDE.md, AGENTS.md.
Left (low, optional): cart.py:3, MAX_ITEMS defined but never enforced.
Merging will: merge into main; then deploy.yml: npx supabase db push --linked
```

The full numbers for this run (time, cost, what each engine caught) are in [docs/evals.md](docs/evals.md).

---

## How it decides

| Severity | Means | Effect |
|---|---|---|
| `blocker` | Data loss, a security hole, a broken deploy | Fix round |
| `high` | Wrong behaviour on a main path | Fix round |
| `medium` | Wrong behaviour on an edge path, or a risky deploy step | Fix round |
| `low` | Real but minor | Listed in the report; doesn't block |

Review priority is fixed:
1. Security: auth, row-level security, secrets, data exposure.
2. Documented invariants.
3. Races.
4. Deploy risk: migrations, workflows.
5. Plain bugs.

A finding is dropped before it reaches anyone if:
- its `file:line` doesn't exist in the PR head;
- it has no concrete failure scenario;
- it has no fix.

Each round, the reviewer sees the earlier rounds' findings and keeps each one's severity unless the risk changed. That stops engines from re-grading a `low` into another fix round.

### Pick a reviewer

| `--reviewer` | Default model | Note |
|---|---|---|
| `codex` | `gpt-6.1-sol`, low effort | Default when the host is Claude; also the default on Codex when Claude isn't installed |
| `claude` | `sonnet` | Default when the host is Codex or Cursor |
| `both` | codex and claude, findings merged | Offered for high-risk PRs; a cross-engine duplicate (same file, within 3 lines) is kept once, at the higher severity |
| `cursor` | `composer-2.5` | Opt-in when you have another tool; the default only on a Cursor-only setup. 45–110 s of startup, and it carries your Cursor account's plugins and MCP tools whatever the sandbox ([evals](docs/evals.md)) |
| `subagent` | An Agent-tool subagent in the same session | Fresh context, same model family; the default on a Claude-only setup |

You pick the engine per run, and you can override the model with `--model` and `--effort`. If the engine fails, pr-gate tells you and offers the next one. It never switches engines silently.

**High-risk PRs get a stronger pass.** When the changed files touch migrations, auth/RLS, CI workflows or payments, codex runs at medium effort and claude runs `opus`, unless you pass `--model` or `--effort`. The reviewer is told which files triggered it, and the report says so.

### Lean runs

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

---

## Safety rules

- **Never merges without your literal `merge` reply**, for that PR, after the green report. Before merging it re-checks that the head SHA and CI are unchanged.
- **Never** pushes, changes repo settings, turns on auto-merge, or uses `--admin`.
- Every script's output is **redacted**: tokens, JWTs, keys and URL passwords are masked before anything is printed, stored or sent.
- **Reviewers are read-only**, and a test enforces it.
- **3 rounds per PR**, kept in `~/.pr-gate/state`.

---

## Running it unattended

Not enabled. [docs/automation.md](docs/automation.md) compares a scheduled local poll, a GitHub Action and a webhook, with cost and risk for each. It recommends a 30-minute local poll, and only after a few weeks of on-demand use.

---

## Repo map

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

## License

[MIT](LICENSE)
