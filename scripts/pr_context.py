"""Gather everything one review round needs for a PR into one JSON file.

  py -3 pr_context.py owner/repo 12 [--work ~/.pr-gate/work]

Checks out the PR head (detached, in pr-gate's own clone, never the author's
worktree), writes <work>/<repo>__<n>.diff and <work>/<repo>__<n>.json, and prints
the JSON path. All text that leaves this script is redacted.
"""
import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact  # noqa: E402

VIEW_FIELDS = ("number,title,body,author,headRefName,headRefOid,baseRefName,url,"
               "isDraft,mergeable,mergeStateStatus,additions,deletions,files")
MARKER = re.compile(r"<!--\s*pr-gate:session=([A-Za-z0-9_-]{1,128})\s+host=([A-Za-z0-9_-]{1,40})\s*-->")
DOC_NAMES = re.compile(r"^(AGENTS|CLAUDE|ARCHITECTURE|DOMAIN_MODEL|SECURITY|CONTRIBUTING)(\..+)?\.md$", re.I)
# Steps that change something outside CI. Local test stacks (`supabase db start`,
# `supabase test db`) are not deploys and stay out of the report.
NOTABLE = re.compile(r"deploy|db push|publish|release|vercel|netlify|eas (submit|update)|"
                     r"terraform apply|kubectl apply|docker push|gh-pages|heroku", re.I)
# Lines worth showing from a failed CI log. The tail of --log-failed is usually
# post-job cleanup, so cut around these instead (measured on factory#10).
FAILURE_LINE = re.compile(r"##\[error\]|\bFAIL(ED)?\b|\bERROR\b|Error:|Traceback|AssertionError|"
                          r"error TS\d+|\bfailed\b|✕|panicked", re.I)
# Rules about who may merge or write, not "folder X is read-only" notes.
MERGE_RULE = re.compile(r"no (commits, )?merges|never merge|do not merge|don't merge|"
                        r"(session|agent|repositor).{0,80}read-only|read-only.{0,80}(session|agent)", re.I)
LOG_CONTEXT_BEFORE, LOG_CONTEXT_AFTER, LOG_MAX_HITS = 12, 4, 4
LOG_MAX_CHARS = 20000
ARTIFACT_MAX_BYTES = 2_000_000
ARTIFACT_MAX = 3


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


def extract_failure(text):
    """The lines around the first failures, not the log's tail."""
    lines = [re.sub(r"^[^\t]*\t[^\t]*\t\S+Z ", "", ln) for ln in (text or "").splitlines()]
    hits = [i for i, ln in enumerate(lines) if FAILURE_LINE.search(ln)][:LOG_MAX_HITS]
    if not hits:
        return "\n".join(lines[-60:])
    keep = set()
    for i in hits:
        keep.update(range(max(0, i - LOG_CONTEXT_BEFORE), min(len(lines), i + LOG_CONTEXT_AFTER + 1)))
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("...")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


def artifact_failures(repo, rid):
    """Failure excerpts from small text artifacts of a failed run (test output
    files a workflow uploads instead of printing)."""
    raw = run(["gh", "api", f"repos/{repo}/actions/runs/{rid}/artifacts",
               "--jq", f".artifacts[] | select(.size_in_bytes < {ARTIFACT_MAX_BYTES}) | .name"], check=False)
    out = []
    for name in [n for n in raw.splitlines() if n.strip()][:ARTIFACT_MAX]:
        with tempfile.TemporaryDirectory() as d:
            run(["gh", "run", "download", rid, "-R", repo, "-n", name, "-D", d], check=False)
            for f in sorted(Path(d).rglob("*")):
                if f.is_file() and f.suffix.lower() in (".txt", ".log", ".out", ".xml"):
                    out.append(f"--- artifact {name}/{f.name} ---\n"
                               + extract_failure(f.read_text(encoding="utf-8", errors="replace")))
    return out


def base_ci(repo, base, checks):
    """Latest run of each failing workflow on the base branch: tells a failure
    the PR caused from one the base already has (or already fixed)."""
    out = {}
    for c in checks:
        wf = c.get("workflow")
        if c.get("bucket") not in ("fail", "cancel") or not wf or wf in out:
            continue
        raw = run(["gh", "run", "list", "-R", repo, "--branch", base, "--workflow", wf, "--limit", "1",
                   "--json", "conclusion,headSha,createdAt"], check=False)
        try:
            last = (json.loads(raw) if raw.strip() else [None])[0]
        except (ValueError, IndexError):
            last = None
        if last:
            out[wf] = {"conclusion": last.get("conclusion"), "sha": (last.get("headSha") or "")[:8],
                       "at": last.get("createdAt")}
    return out


def behind_base(repo, head_sha, base):
    raw = run(["gh", "api", f"repos/{repo}/compare/{head_sha}...{base}", "--jq", ".ahead_by"], check=False)
    return int(raw.strip()) if raw.strip().isdigit() else None


def repo_docs(root):
    root = Path(root)
    out, seen = [], set()
    for p in sorted(list(root.glob("*.md")) + list(root.glob("docs/*.md")),
                    key=lambda p: p.relative_to(root).as_posix()):
        if not DOC_NAMES.match(p.name):
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in seen:  # same file at the root and in docs/: read it once
            continue
        seen.add(digest)
        out.append(p.relative_to(root).as_posix())
    return sorted(out)


def merge_rules(root, docs):
    """Lines in the repo's own docs that restrict merging or writing by outside sessions."""
    out = []
    for d in docs:
        for n, line in enumerate((Path(root) / d).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if MERGE_RULE.search(line):
                out.append(f"{d}:{n}: {line.strip()[:160]}")
    return out[:6]


def glob_match(path, pattern):
    """GitHub Actions path glob: ** crosses folders, * does not."""
    rx = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            rx += ".*"
            i += 2
        elif pattern[i] == "*":
            rx += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            rx += "[^/]"
            i += 1
        else:
            rx += re.escape(pattern[i])
            i += 1
    return re.fullmatch(rx, path) is not None


def _yaml_list(block, key):
    """Items of `key:` inside a block: inline [a, 'b'] or a dash list. None if absent."""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^(\s*){re.escape(key)}:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(2).strip()
        if rest.startswith("["):
            return [s.strip().strip("'\"") for s in rest.strip("[]").split(",") if s.strip()]
        if rest:
            return [rest.strip("'\"")]
        items = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(m.group(1)):
                break
            d = re.match(r"^\s*-\s*(.+)$", nxt)
            if d:
                items.append(d.group(1).strip().strip("'\""))
        return items
    return None


def _push_block(text):
    """The body under `on: push`, "" for a bare `on: push`, None if push is not a trigger."""
    if re.search(r"(?m)^on:[ \t]*(push\b|\[[^\]]*\bpush\b)", text):  # same line only
        return ""
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
        return "\n".join(block)
    return None


def _push_triggers(text, base, files=None):
    # ponytail: line-based YAML read, enough for branches/paths filters.
    # Swap for a real YAML parser if a workflow ever fools it.
    body = _push_block(text)
    if body is None:
        return False
    branches, ignore = _yaml_list(body, "branches"), _yaml_list(body, "branches-ignore")
    if branches is not None and not any(fnmatch.fnmatch(base, b) for b in branches):
        return False
    if ignore and any(fnmatch.fnmatch(base, b) for b in ignore):
        return False
    paths, pignore = _yaml_list(body, "paths"), _yaml_list(body, "paths-ignore")
    if files is not None:
        if paths is not None and not any(glob_match(f, p) for f in files for p in paths):
            return False
        if pignore and all(any(glob_match(f, p) for p in pignore) for f in files):
            return False
    return True


def merge_triggers(root, base, files=None):
    """Workflows that run when the PR lands on `base` (honouring branches and
    paths filters), with the steps that change something outside CI."""
    wf = Path(root) / ".github" / "workflows"
    out = []
    for p in sorted(list(wf.glob("*.yml")) + list(wf.glob("*.yaml"))) if wf.is_dir() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        closed = re.search(r"pull_request(_target)?:[\s\S]{0,200}?closed", text)
        if _push_triggers(text, base, files) or closed:
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
    base = view.get("baseRefName") or "main"
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
        logs.append(f"--- run {rid}: failure excerpt ---\n" + extract_failure(text))
        logs.extend(artifact_failures(repo, rid))
    root = work / f"{safe}__checkout"
    checkout(repo, pr, root)
    files = [f.get("path") for f in view.get("files") or []]
    docs = repo_docs(root)
    ctx = {
        "repo": repo, "pr": pr, "url": view.get("url"), "title": view.get("title"),
        "body": redact(view.get("body") or ""), "author": (view.get("author") or {}).get("login"),
        "head": view.get("headRefName"), "head_sha": view.get("headRefOid"),
        "base": base, "draft": view.get("isDraft"),
        "mergeable": view.get("mergeable"), "merge_state": view.get("mergeStateStatus"),
        "behind_base": behind_base(repo, view.get("headRefOid"), base),
        "size": {"files": len(files), "additions": view.get("additions"),
                 "deletions": view.get("deletions")},
        "files": files,
        "owner": parse_marker(view.get("body")),
        "ci": ci_state(checks),
        "checks": [{k: c.get(k) for k in ("name", "bucket", "workflow")} for c in checks],
        "base_ci": base_ci(repo, base, checks),
        "failed_logs": redact("\n".join(logs))[:LOG_MAX_CHARS],
        "checkout": str(root), "diff_file": str(diff_file),
        "docs": docs,
        "merge_rules": merge_rules(root, docs),
        "merge_triggers": merge_triggers(root, base, files),
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
    print(json.dumps({k: ctx[k] for k in ("context_file", "ci", "base_ci", "head_sha", "draft",
                                          "merge_state", "behind_base", "owner", "docs",
                                          "merge_rules", "merge_triggers", "size")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
