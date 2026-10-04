"""Mask secret-looking strings before any text is printed, stored or sent to a reviewer.

Usage: py -3 redact.py < in.txt > out.txt
"""
import re
import sys

MASK = "[REDACTED]"

PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsb_(?:secret|publishable)_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
]
# user:password@ inside URLs -> keep scheme and host.
URL_CREDS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@", re.I)
# NAME=value / "name": "value" where NAME looks secret.
NAMED = re.compile(
    r"""(?ix)
    ( ["']?[A-Z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|apikey|service[_-]?role[_-]?key|private[_-]?key|access[_-]?key)[A-Z0-9_.-]*["']?
      \s*[:=]\s*["']? )
    ( (?![\d.]+\b)[^\s"',]{4,} )   # pure numbers (token counts, ports) are not secrets
    """)


def redact(text):
    if not text:
        return text
    for p in PATTERNS:
        text = p.sub(MASK, text)
    text = URL_CREDS.sub(lambda m: m.group(1) + MASK + "@", text)
    text = NAMED.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) == MASK else MASK), text)
    return text


if __name__ == "__main__":
    sys.stdout.write(redact(sys.stdin.read()))
