"""Hand a fix request to the PR's owner session through a file, from any host.

  py -3 handoff.py send  <owner/repo> <N> --sha <head> --body-file fix.md
  py -3 handoff.py taken <owner/repo> <N> --sha <head>

`send` writes ~/.pr-gate/inbox/<key>.<sha8>.md. The inbox mod (mod/), loaded in
the owner's Claude Code session, finds the file for the PR its branch has,
marks it `.taken` and starts a turn with it once the session is idle.
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


def send(repo, pr, sha, body):
    p = inbox_file(repo, pr, sha)
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
    ap.add_argument("--body-file")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", a.repo):
        ap.error("repo must be owner/name")
    if not re.fullmatch(r"[0-9a-f]{8,40}", a.sha):
        ap.error("sha must be hex")
    if a.cmd == "taken":
        print(json.dumps({"taken": taken(a.repo, a.pr, a.sha)}))
        return 0
    if not a.body_file:
        ap.error("send needs --body-file")
    body = Path(a.body_file).read_text(encoding="utf-8-sig")
    print(json.dumps({"inbox_file": str(send(a.repo, a.pr, a.sha, body))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
