# pr-gate (Codex, Cursor)

Same rules as [CLAUDE.md](CLAUDE.md). On Codex and Cursor there is no app
`send_message`, so fix requests go to `~/.pr-gate/inbox/` (`scripts/handoff.py`,
picked up by the inbox mod in the owner's Claude Code session) and to the PR as
a comment (see `SKILL.md`, Owner routing).

Resuming work: start at [docs/TAKEOVER.md](docs/TAKEOVER.md).
