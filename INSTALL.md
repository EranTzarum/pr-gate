# Installing pr-gate (for the agent doing it)

The user pasted this repo and asked you to install it. Do these steps in order, report each
result, and ask before step 5 (it changes how their sessions load plugins).

1. **Find the tools.** Run `claude --version`, `codex --version`, `cursor-agent --version`
   and `gh auth status`. `gh` must be logged in; if not, tell the user to run `gh auth login`
   and stop. Python: `py -3 --version` on Windows, `python3 --version` elsewhere.
2. **Clone once** into the skills folder of the first tool found:
   `git clone https://github.com/EranTzarum/pr-gate.git <dir>`, where `<dir>` is
   `~/.claude/skills/pr-gate`, `~/.codex/skills/pr-gate` or `~/.cursor/skills/pr-gate`
   (on Windows, `~` is `%USERPROFILE%`). If it already exists, `git -C <dir> pull` instead.
3. **The other tools** the user has: copy the folder there too (or a directory link:
   `ln -s` on macOS/Linux, `cmd /c mklink /J` on Windows). Never remove a link with a
   recursive delete; on Windows use `cmd /c rmdir "<link>"`.
4. **Verify:** inside the folder, `python3 -m unittest discover tests` (`py -3` on Windows).
   Expected: `Ran 72 tests ... OK`. No network is used.
5. **Optional, Claude Code only, ask first:** the inbox mod delivers fix requests to the
   session that has the PR's branch checked out. Install by linking `mod/` as
   `~/.claude/skills/pr-gate-inbox`, then `claude plugin list` must show
   `pr-gate-inbox@skills-dir` as loaded. Open sessions pick it up after `/reload-plugins`.
   It is not required: without it, fix requests go out as an app message or a PR comment.
6. **Tell the user how to start:** in a session inside a repo, `/pr-gate owner/repo <PR number>`
   (in Codex or Cursor: "run pr-gate on PR <number>"). The first run asks which reviewer to
   use. Nothing merges without their literal `merge` reply.
