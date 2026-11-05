"""Watch https://boltbook.fun/skill.md for changes.

The copy kept in skill_latest.md is the baseline. When the published file differs, it logs a loud
"[SKILL UPDATE]" line with what changed, appends the diff to skill_changes.txt and replaces the baseline,
so the next change is compared against the new text.
"""
import datetime
import difflib
import hashlib
import os
import time
import urllib.request

from . import api

URL = "https://boltbook.fun/skill.md"
LOCAL = os.path.join(api.HERE, "skill_latest.md")
CHANGES = os.path.join(api.HERE, "skill_changes.txt")
EVERY = 600  # seconds between checks
SHOWN = 40   # changed lines printed to the log


def fetch():
    req = urllib.request.Request(URL, headers={"user-agent": "agentwild/3.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def read_local():
    try:
        with open(LOCAL, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def check():
    """True when the published skill.md changed since the last check (and the change was recorded)."""
    new = fetch()
    old = read_local()
    if hashlib.sha256(new.encode()).digest() == hashlib.sha256(old.encode()).digest():
        return False
    diff = [l for l in difflib.unified_diff(old.splitlines(), new.splitlines(), "old", "new", lineterm="", n=0)
            if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    api.warning("[SKILL UPDATE] skill.md changed at %s: %d lines differ (%d bytes -> %d)" % (stamp, len(diff), len(old), len(new)))
    for l in diff[:SHOWN]:
        api.warning("[SKILL UPDATE]   " + l[:200])
    with open(CHANGES, "a", encoding="utf-8") as f:
        f.write("== %s ==\n%s\n\n" % (stamp, "\n".join(diff)))
    with open(LOCAL, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    return True


def loop():
    while True:
        try:
            check()
        except Exception as e:
            api.error("[skill watch] check failed: %r" % e)
        time.sleep(EVERY)
