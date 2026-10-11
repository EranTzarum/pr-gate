# How pr-gate decides

<p align="center">
  <img src="assets/loop.svg" alt="New PR or push, wait for CI, fresh review, fix request to the owner session for up to 3 rounds, then a green report and your merge reply" width="980">
</p>

## Why a fresh reviewer

The agent that wrote a change is the worst judge of it. A second model, with a fresh context, keeps finding real defects. Green CI is not enough either: local checks can pass while the deploy breaks.

Doing this by hand means reading diffs, pasting findings into the right chat, and checking back after every push. pr-gate runs that loop and stops only when it needs you:

| It stops for you when | You see |
|---|---|
| CI is green and only low findings remain | What the PR does, what was checked, what merging will trigger |
| 3 rounds pass and it still isn't clean | What was fixed, what's still open, your options |
| No session owns the branch | The fix request posted on the PR, and which session should pick it up |

## Severities and priority

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

Back to the [README](../README.md).
