"""Gather everything one review round needs for a PR into one JSON file.

  py -3 pr_context.py owner/repo 12 [--work ~/.pr-gate/work]

Checks out the PR head (detached, in pr-gate's own clone, never the author's
worktree), writes <work>/<repo>__<n>.diff and <work>/<repo>__<n>.json, and prints
the JSON path. All text that leaves this script is redacted.
"""
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact  # noqa: E402

VIEW_FIELDS = ("number,title,body,author,headRefName,headRefOid,baseRefName,url,"
               "isDraft,mergeable,additions,deletions,files")
MARKER = re.compile(r"<!--\s*pr-gate:session=([A-Za-z0-9_-]{1,128})\s+host=([A-Za-z0-9_-]{1,40})\s*-->")
DOC_NAMES = re.compile(r"^(AGENTS|CLAUDE|ARCHITECTURE|DOMAIN_MODEL|SECURITY|CONTRIBUTING)(\..+)?\.md$", re.I)
NOTABLE = re.compile(r"deploy|db push|migrat|publish|release|vercel|netlify|eas (build|submit|update)|"
                     r"terraform|kubectl|docker push|supabase|fly |heroku", re.I)
LOG_TAIL_LINES = 120
LOG_MAX_CHARS = 20000


def run(args, cwd=None, check=True):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {redact(p.stderr.strip())[:400]}")
    return p.stdout


def parse_marker(body):
    m = MARKER.search(body or "")
    return {"session": m.group(1), "host": m.group(2)} if m else None


def ci_state(checks):
    buckets = {c.get("bucket") for c in checks}
    if not checks:
        return "none"
    if buckets & {"fail", "cancel"}:
        return "fail"
    if "pending" in buckets:
        return "pending"
    return "pass"


def failed_run_ids(checks):
    ids = []
    for c in checks:
        if c.get("bucket") not in ("fail", "cancel"):
            continue
        m = re.search(r"/actions/runs/(\d+)", c.get("link") or "")
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def repo_docs(root):
    root = Path(root)
    out = []
    for p in list(root.glob("*.md")) + list(root.glob("docs/*.md")):
        if DOC_NAMES.match(p.name):
            out.append(p.relative_to(root).as_posix())
    return sorted(out)


def _push_triggers(text, base):
    # ponytail: line-based YAML read, enough for `on: push` / push.branches lists.
    # Swap for a real YAML parser if a workflow ever fools it.
    if re.search(r"(?m)^on:\s*(push\b|\[[^\]]*\bpush\b)", text):
        return True
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)push:\s*$", line)
        if not m:
            continue
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(m.group(1)):
                break
            block.append(nxt)
        body = "\n".join(block)
        if "branches" not in body:
            return True
        names = re.findall(r"[\w./*-]+", body.split("branches", 1)[1])
        if "branches-ignore" in body:
            return not any(fnmatch.fnmatch(base, n) for n in names)
        return any(fnmatch.fnmatch(base, n) for n in names)
    return False


def merge_triggers(root, base):
    """Workflows that run when the PR lands on `base`, with their notable steps."""
    wf = Path(root) / ".github" / "workflows"
    out = []
    for p in sorted(list(wf.glob("*.yml")) + list(wf.glob("*.yaml"))) if wf.is_dir() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        closed = re.search(r"pull_request(_target)?:[\s\S]{0,200}?closed", text)
        if _push_triggers(text, base) or closed:
            notable = [ln.strip() for ln in text.splitlines()
                       if re.search(r"\b(run|uses):", ln) and NOTABLE.search(ln)]
            out.append({"file": p.name, "notable": notable})
    return out


def checkout(repo, pr, dest):
    if not (dest / ".git").exists():
        run(["gh", "repo", "clone", repo, str(dest), "--", "--filter=blob:none", "--no-checkout"])
    run(["git", "-C", str(dest), "fetch", "--quiet", "origin", f"pull/{pr}/head"])
    run(["git", "-C", str(dest), "checkout", "--quiet", "--detach", "--force", "FETCH_HEAD"])


def gather(repo, pr, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", repo.replace("/", "__"))
    view = json.loads(run(["gh", "pr", "view", str(pr), "-R", repo, "--json", VIEW_FIELDS]))
    # gh pr checks exits non-zero while checks fail or pend; the JSON is still valid.
    raw = run(["gh", "pr", "checks", str(pr), "-R", repo, "--json", "name,state,bucket,link,workflow"],
              check=False)
    try:
        checks = json.loads(raw) if raw.strip() else []
    except ValueError:
        checks = []
    diff_file = work / f"{safe}__{pr}.diff"
    diff_file.write_text(redact(run(["gh", "pr", "diff", str(pr), "-R", repo])), encoding="utf-8")
    logs = []
    for rid in failed_run_ids(checks):
        text = run(["gh", "run", "view", rid, "-R", repo, "--log-failed"], check=False)
        logs.append(f"--- run {rid} (last {LOG_TAIL_LINES} lines) ---\n"
                    + "\n".join(text.splitlines()[-LOG_TAIL_LINES:]))
    root = work / f"{safe}__checkout"
    checkout(repo, pr, root)
    ctx = {
        "repo": repo, "pr": pr, "url": view.get("url"), "title": view.get("title"),
        "body": redact(view.get("body") or ""), "author": (view.get("author") or {}).get("login"),
        "head": view.get("headRefName"), "head_sha": view.get("headRefOid"),
        "base": view.get("baseRefName"), "draft": view.get("isDraft"),
        "mergeable": view.get("mergeable"),
        "size": {"files": len(view.get("files") or []), "additions": view.get("additions"),
                 "deletions": view.get("deletions")},
        "files": [f.get("path") for f in view.get("files") or []],
        "owner": parse_marker(view.get("body")),
        "ci": ci_state(checks),
        "checks": [{k: c.get(k) for k in ("name", "bucket", "workflow")} for c in checks],
        "failed_logs": redact("\n".join(logs))[-LOG_MAX_CHARS:],
        "checkout": str(root), "diff_file": str(diff_file),
        "docs": repo_docs(root),
        "merge_triggers": merge_triggers(root, view.get("baseRefName") or "main"),
    }
    out = work / f"{safe}__{pr}.json"
    out.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    ctx["context_file"] = str(out)
    return ctx


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("pr", type=int)
    ap.add_argument("--work", default=str(Path.home() / ".pr-gate" / "work"))
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", a.repo):
        ap.error("repo must be owner/name")
    ctx = gather(a.repo, a.pr, a.work)
    print(json.dumps({k: ctx[k] for k in ("context_file", "ci", "head_sha", "owner",
                                          "docs", "merge_triggers", "size")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
