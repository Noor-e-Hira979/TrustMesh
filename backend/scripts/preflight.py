#!/usr/bin/env python
"""Pre-push secret scan.

Refuses to let a commit go out carrying credentials. Cheap, offline, and it targets the
specific secrets this project actually uses.

    python scripts/preflight.py            # scan the working tree
    python scripts/preflight.py --staged   # scan only what git is about to commit

Exit codes: 0 clean, 1 secrets found, 2 not a git repository.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Sibling module; running this file directly puts its directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_encoding  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

#: Never committed, whatever they contain.
BLOCKED_FILES = {".env", ".env.local", ".env.production", "keystore.json"}

#: Never committed, by extension.
BLOCKED_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".keystore"}

#: Directories never worth scanning.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "artifacts",
    "cache",
    ".vite",
    "typechain",
    "typechain-types",
}

GREEN, RED, YELLOW, CYAN, DIM, BOLD, RESET = (
    "\033[92m",
    "\033[91m",
    "\033[93m",
    "\033[96m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)

#: (label, regex) - a match anywhere in a tracked text file is a finding.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Ethereum private key (0x + 64 hex)", re.compile(r"0x[a-fA-F0-9]{64}")),
    ("MNEMONIC seed phrase", re.compile(r"\b(mnemonic|seed phrase)\b\s*[:=]", re.I)),
    (
        "Google API key",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    (
        "assigned private key",
        re.compile(r"(?i)^\s*(BLOCKCHAIN_PRIVATE_KEY|DEPLOYER_PRIVATE_KEY|GEMINI_API_KEY)\s*=\s*\S+"),
    ),
]

#: Values that are obviously placeholders and must not trip the scanner.
SAFE_VALUES = {
    "0x" + "0" * 64,
    "PLACEHOLDER_KEY",
    "CHANGEME",
    "CHANGE_ME",
    "YOUR_API_KEY",
    "YOUR_API_KEY_HERE",
    "REPLACE_ME",
    "none",
    "null",
    "n/a",
    "",
}

#: A value that is only punctuation / an ellipsis / an unfilled template reference.
_PLACEHOLDER_VALUE = re.compile(
    r"^(?:0x)?(?:x*|\.{2,}|…+|<[^>]*>|\$\{[^}]*\}|-+|_+|\s)+$"
)

#: Documentation lines that describe a variable without assigning it a real value.
_DOC_ASSIGNMENT = re.compile(
    r"(?i)^(BLOCKCHAIN_PRIVATE_KEY|DEPLOYER_PRIVATE_KEY|GEMINI_API_KEY|"
    r"CREDENTIAL_HMAC_SECRET|BLOCKCHAIN_RPC_URL|BLOCKCHAIN_CONTRACT_ADDRESS|"
    r"REGISTRY_CONTRACT_ADDRESS)\s*="
)


def git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60
        )
        return proc.returncode, proc.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:  # pragma: no cover
        return 1, ""


def is_git_repo() -> bool:
    code, out = git("rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def iter_files(staged: bool) -> list[Path]:
    if staged:
        code, out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
        if code != 0:
            return []
        names = [line.strip() for line in out.splitlines() if line.strip()]
        return [PROJECT_ROOT / n for n in names]

    # Everything git could actually commit: tracked files plus untracked files that are
    # not ignored. Using a plain directory walk instead reported ignored files such as
    # backend/.env as "blocked file type", which is a false positive - that file is
    # exactly the thing that must stay untracked, and the separate git checks above
    # already prove it is untracked. A pre-push gate that always fails gets ignored.
    code, out = git("ls-files", "--cached", "--others", "--exclude-standard")
    if code == 0 and out.strip():
        names = [line.strip() for line in out.splitlines() if line.strip()]
        files = [PROJECT_ROOT / n for n in names]
    else:
        # Not a git repository, or git refused: fall back to a filesystem walk.
        files = []
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
    return files


def looks_like_placeholder(line: str) -> bool:
    """True when an assignment line carries no real secret.

    Handles the three shapes that appear in real repositories: a bare placeholder
    (``KEY=PLACEHOLDER``), documentation prose with a trailing comment
    (``KEY=0x...   # never commit``), and an unfilled template reference
    (``KEY=${SECRET}``).
    """
    stripped = line.strip().strip("\"'")
    if not stripped:
        return True
    if "PLACEHOLDER" in stripped.upper():
        return True
    if "=" not in stripped:
        return False

    name, _, value = stripped.partition("=")
    if not _DOC_ASSIGNMENT.match(stripped):
        return False
    # Drop a trailing comment so "0x...  # note" is judged on the value alone.
    value = value.split("#", 1)[0].strip().strip("\"'")
    if value in SAFE_VALUES:
        return True
    return bool(_PLACEHOLDER_VALUE.match(value))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for secrets before pushing.")
    parser.add_argument("--staged", action="store_true", help="only scan staged changes")
    args = parser.parse_args()

    print(f"\n{CYAN}TrustMesh pre-push secret scan{RESET}")
    print(f"{DIM}root: {PROJECT_ROOT}{RESET}")

    if not is_git_repo():
        print(f"\n{YELLOW}Not a git repository yet - skipping the git checks.{RESET}")
        print(f"{DIM}Run 'git init' first, then re-run this scan.{RESET}\n")
        return 2

    code, _ = git("ls-files", "--error-unmatch", ".env")
    if code == 0:
        print(f"\n{RED}{BOLD}FAIL{RESET} .env is tracked by git.")
        print(f"{RED}      Run:  git rm --cached backend/.env{RESET}")
        print(f"{RED}      Then confirm .gitignore contains '.env'{RESET}\n")
        return 1

    code, tracked_envs = git("ls-files")
    bad_tracked = [
        line.strip()
        for line in tracked_envs.splitlines()
        if line.strip() and Path(line.strip()).name in BLOCKED_FILES
    ]
    if bad_tracked:
        print(f"\n{RED}{BOLD}FAIL{RESET} secret-bearing files are tracked:")
        for name in bad_tracked:
            print(f"      {RED}{name}{RESET}")
        print(f"{RED}      Untrack them:  git rm --cached <file>{RESET}\n")
        return 1

    files = iter_files(args.staged)
    findings: list[tuple[Path, int, str, str]] = []

    for path in files:
        if not path.exists() or not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        name = path.name

        if name in BLOCKED_FILES or path.suffix.lower() in BLOCKED_SUFFIXES:
            findings.append((rel, 0, "blocked file type", f"{name} must never be committed"))
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if looks_like_placeholder(stripped):
                continue
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    snippet = stripped[:70]
                    findings.append((rel, lineno, label, snippet))
                    break

    print(f"\n{DIM}files scanned: {len(files)}{RESET}\n")

    if findings:
        print(f"{RED}{BOLD}{len(findings)} potential secret(s) found:{RESET}\n")
        for rel, lineno, label, snippet in findings:
            location = f"{rel}:{lineno}" if lineno else str(rel)
            print(f"  {RED}{BOLD}{label}{RESET}")
            print(f"    {DIM}{location}{RESET}")
            print(f"    {DIM}{snippet}{RESET}\n")
        print(f"{YELLOW}These may be false positives (fixtures, docs, test data).{RESET}")
        print(f"{YELLOW}Confirm each one before committing.{RESET}\n")
        secret_problems = 1
    else:
        print(f"{GREEN}No secrets detected.{RESET}")
        print(f"{DIM}.env is untracked and ignored. Safe to push.{RESET}\n")
        secret_problems = 0

    # Encoding gate. PowerShell here-strings and Set-Content silently re-encoded several
    # source files in this repo, corrupting user-visible copy such as an em dash. It is
    # invisible in a diff and in review, so it is checked mechanically on every push.
    print(f"\n{DIM}{'-' * 62}{RESET}")
    print(f"{CYAN}encoding check{RESET}")
    corrupted = check_encoding.scan(PROJECT_ROOT, fix=False)
    if corrupted:
        print(f"{RED}{BOLD}{corrupted} corrupted line(s) found.{RESET}")
        print(f"{YELLOW}Repair them with:{RESET}")
        print(f"{YELLOW}  python backend/scripts/check_encoding.py --fix{RESET}\n")
    else:
        print(f"{GREEN}No encoding corruption detected.{RESET}\n")

    return secret_problems | (1 if corrupted else 0)


if __name__ == "__main__":
    sys.exit(main())
