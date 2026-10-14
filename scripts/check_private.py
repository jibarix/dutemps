"""Refuses a commit that would publish your records or anything you list as private.

    python scripts/check_private.py            check the staged files
    python scripts/check_private.py --all      also check working copies and untracked files
    python scripts/check_private.py --install  run the staged check before each commit

Blocked always: anything under data/, local/, scratch/, backups/ or imports/,
invoice profiles, databases, .env files, PDFs and spreadsheets. Blocked when listed: the terms in local/private-terms.txt, one per
line (whole words, case-insensitive; '#' starts a comment). Files that aren't
UTF-8, UTF-16 or UTF-32 text can't be read, so they are refused. Put your name, email,
phone, clients and anything else that should never leave your machine there.
The file itself is ignored by git.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMS = os.path.join(ROOT, "local", "private-terms.txt")
BLOCKED = re.compile(r"(^|/)(data|local|scratch|backups|imports)/|(^|/)invoice_profile[^/]*\.json$|\.(db|db-shm|db-wal|sqlite3?|env|pdf|xlsx?|csv\.bak)$"
                     r"|(^|/)\.env(\.[^/]*)?$", re.I)
ALLOWED = {"tests/fixtures/sample_month.csv", "examples/invoice_profile.json"}
HOOK = "#!/bin/sh\nexec python scripts/check_private.py\n"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          check=True).stdout


def terms():
    if not os.path.exists(TERMS):
        return []
    with open(TERMS, encoding="utf-8") as f:
        lines = (ln.split("#", 1)[0].strip() for ln in f)
        return [t for t in lines if t]


def files(all_files):
    if all_files:
        out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    else:
        out = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    return [p for p in out.decode("utf-8").split("\0") if p]


def versions(path, all_files):
    """The staged version, and with --all the working copy too when it differs."""
    out = []
    try:
        out.append(("staged", git("show", f":{path}")))
    except subprocess.CalledProcessError:   # untracked: only on disk
        pass
    if all_files and os.path.isfile(os.path.join(ROOT, path)):
        with open(os.path.join(ROOT, path), "rb") as f:
            disk = f.read()
        if not out or disk != out[0][1]:
            out.append(("working copy", disk))
    return out


def text(data):
    """The text of a file, or None when it is binary or not UTF-8/16/32."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32"),
                     (b"\x00\x00\xfe\xff", "utf-32"), (b"\xff\xfe", "utf-16"),
                     (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def install():
    hooks = git("rev-parse", "--git-path", "hooks").decode().strip()
    path = os.path.join(ROOT, hooks, "pre-commit")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == HOOK:
                print(f"already installed: {path}")
                return 0
        print(f"{path} exists; add this line to it yourself:\n"
              "  python scripts/check_private.py", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK)
    os.chmod(path, 0o755)
    print(f"installed {path}")
    return 0


def main(argv):
    if "--install" in argv:
        return install()
    all_files = "--all" in argv
    words = terms()
    # whole words only, so "Sam" doesn't match "Sample"
    pat = re.compile("|".join(r"(?<!\w)" + re.escape(t) + r"(?!\w)" for t in words),
                     re.I) if words else None
    problems = []
    for path in files(all_files):
        if BLOCKED.search(path) and path not in ALLOWED:
            problems.append(f"{path}: private file type or folder")
            continue
        if path == "local/private-terms.txt":
            continue
        if pat and pat.search(path):
            problems.append(f"{path}: file name matches a private term")
        for which, raw in versions(path, all_files):
            data = text(raw)
            if data is None:
                problems.append(f"{path} ({which}): binary or not UTF-8/16/32 text, "
                                "so it can't be checked")
                continue
            for n, line in enumerate(data.splitlines(), 1):
                m = pat.search(line) if pat else None
                if m:
                    problems.append(f"{path}:{n} ({which}): contains {m.group(0)!r}")
    if problems:
        print("check_private: not committing these:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    if not words:
        print("check_private: no local/private-terms.txt, so only file types were "
              "checked", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
