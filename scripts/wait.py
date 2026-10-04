"""Block until a PR changes state. Run it in the background; it exits when done.

  py -3 wait.py ci   owner/repo N              # until no check is pending
  py -3 wait.py push owner/repo N --since SHA  # until the head SHA differs from SHA

Exit 0 with one JSON line on success, 4 on timeout. A process that sleeps is
cheaper than a model that polls.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_context  # noqa: E402

INTERVAL_S = 60


def checks(repo, pr):
    raw = pr_context.run(["gh", "pr", "checks", str(pr), "-R", repo, "--json", "name,bucket"], check=False)
    try:
        return json.loads(raw) if raw.strip() else []
    except ValueError:
        return [{"bucket": "pending"}]  # transient gh error: keep waiting


def head(repo, pr):
    v = json.loads(pr_context.run(["gh", "pr", "view", str(pr), "-R", repo, "--json", "headRefOid,state"]))
    return v["headRefOid"], v["state"]


def wait(mode, repo, pr, since=None, timeout_s=3600, interval_s=INTERVAL_S, sleep=time.sleep):
    deadline = time.monotonic() + timeout_s
    while True:
        if mode == "ci":
            state = pr_context.ci_state(checks(repo, pr))
            if state != "pending":
                return {"ci": state}
        else:
            sha, pr_state = head(repo, pr)
            if pr_state != "OPEN":
                return {"state": pr_state, "sha": sha}
            if sha != since:
                return {"sha": sha}
        if time.monotonic() >= deadline:
            return None
        sleep(interval_s)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["ci", "push"])
    ap.add_argument("repo")
    ap.add_argument("pr", type=int)
    ap.add_argument("--since")
    ap.add_argument("--timeout-min", type=int, default=60)
    a = ap.parse_args(argv)
    if a.mode == "push" and not a.since:
        ap.error("push needs --since SHA")
    res = wait(a.mode, a.repo, a.pr, a.since, a.timeout_min * 60)
    print(json.dumps(res or {"timeout": True}))
    return 0 if res else 4


if __name__ == "__main__":
    sys.exit(main())
