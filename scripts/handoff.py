"""Hand a fix request to the PR's owner session through a file, from any host.

  py -3 handoff.py send  <owner/repo> <N> --sha <head> --branch <head branch> --body-file fix.md
  py -3 handoff.py taken <owner/repo> <N> --sha <head>

`send` writes ~/.pr-gate/inbox/<key>.<sha8>.md; its first line names the repo, PR and
head branch. The inbox mod (mod/) in the owner's Claude Code session (the one whose cwd,
or a repo folder under its cwd that it works in, has that branch checked out) marks it
`.taken` and starts a turn with it once the session is idle.
`taken` prints {"taken": true|false}: whether a session picked it up.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import findings  # noqa: E402
from redact import redact  # noqa: E402


def inbox_file(repo, pr, sha):
    d = findings.home() / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{findings.key(repo, pr)}.{sha[:8]}.md"


def send(repo, pr, sha, branch, body):
    p = inbox_file(repo, pr, sha)
    body = f"<!-- pr-gate repo={repo} pr={int(pr)} branch={branch} -->\n" + body  # read by mod/hooks/register.ts
    tmp = p.with_suffix(".tmp")  # the mod only reads *.md: write whole, then rename
    tmp.write_text(redact(body), encoding="utf-8")
    os.replace(tmp, p)
    Path(str(p) + ".taken").unlink(missing_ok=True)  # a re-send is new mail
    return p


def taken(repo, pr, sha):
    return Path(str(inbox_file(repo, pr, sha)) + ".taken").exists()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("send", "taken"))
    ap.add_argument("repo")
    ap.add_argument("pr", type=int)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--branch", help="the PR's head branch (send)")
    ap.add_argument("--body-file")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", a.repo):
        ap.error("repo must be owner/name")
    if not re.fullmatch(r"[0-9a-f]{8,40}", a.sha):
        ap.error("sha must be hex")
    if a.cmd == "taken":
        print(json.dumps({"taken": taken(a.repo, a.pr, a.sha)}))
        return 0
    if not a.body_file or not a.branch:
        ap.error("send needs --branch and --body-file")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", a.branch):
        ap.error("branch has unexpected characters")
    body = Path(a.body_file).read_text(encoding="utf-8-sig")
    print(json.dumps({"inbox_file": str(send(a.repo, a.pr, a.sha, a.branch, body))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
