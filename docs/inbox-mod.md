# The inbox mod (Claude Code only, optional)

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

Back to the [README](../README.md).
