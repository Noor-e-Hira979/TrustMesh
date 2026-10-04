"""Detect mojibake: text that was UTF-8 encoded and then decoded as cp1252/latin-1.

Motivation: PowerShell here-strings and `Set-Content` silently re-encoded several source
files in this repo, turning characters such as U+00A7 (section sign) into runs of Latin-1
supplement characters. The corruption is invisible in a diff but breaks user-visible copy.

Detection is a round-trip test rather than a blocklist, because the legitimate non-ASCII
characters in this project (em dash, ellipsis, curly quotes, section sign, middot, <=)
overlap the byte ranges that mojibake produces. A line is corrupt only when re-encoding it
as cp1252 and decoding as UTF-8 succeeds *and* yields different text.

Usage:
    python scripts/check_encoding.py                 # scan the repo
    python scripts/check_encoding.py --fix           # repair in place
    python scripts/check_encoding.py backend/app     # scan a subtree
"""

from __future__ import annotations

import argparse
import pathlib
import sys

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "cache",
    "coverage",
    "dist",
    "node_modules",
    "venv",
}
TEXT_SUFFIXES = {
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".sol",
    ".txt",
    ".yaml",
    ".yml",
}
REPLACEMENT = "\ufffd"
# Depth cap for repeated round-trips; real files here need at most three passes.
MAX_PASSES = 4


def candidate_files(root: pathlib.Path) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def repair(line: str) -> tuple[str, int]:
    """Undo cp1252/latin-1 mis-decoding. Returns the fixed line and pass count."""
    passes = 0
    current = line
    for _ in range(MAX_PASSES):
        try:
            candidate = current.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == current or REPLACEMENT in candidate:
            break
        current = candidate
        passes += 1
    return current, passes


def scan(root: pathlib.Path, fix: bool) -> int:
    problems = 0
    for path in candidate_files(root):
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"  {path}: not valid UTF-8 ({exc})")
            problems += 1
            continue

        # Every line is tested. An earlier version skipped whole files that encoded
        # cleanly as cp1252, which is exactly the set of mojibake files, so the check
        # silently passed on the corruption it was written to catch.
        hits: list[tuple[int, str, str, int]] = []
        for number, line in enumerate(original.splitlines(), 1):
            if REPLACEMENT not in line:
                try:
                    line.encode("cp1252")
                except UnicodeEncodeError:
                    # Legitimate characters outside cp1252 (CJK, box drawing, arrows)
                    # cannot be mis-decoded output, so there is nothing to repair.
                    continue
            fixed, passes = repair(line)
            if passes:
                hits.append((number, line, fixed, passes))

        if not hits:
            continue

        problems += len(hits)
        if fix:
            lines = original.splitlines(keepends=True)
            for number, _, fixed, _ in hits:
                ending = "\n" if lines[number - 1].endswith("\n") else ""
                lines[number - 1] = fixed + ending
            path.write_text("".join(lines), encoding="utf-8")
            print(f"  fixed {len(hits)} line(s) in {path}")
        else:
            for number, before, after, passes in hits[:3]:
                print(f"  {path}:{number} ({passes} pass(es))")
                print(f"    before: {before.strip()[:88]}")
                print(f"    after:  {after.strip()[:88]}")
            if len(hits) > 3:
                print(f"    ... and {len(hits) - 3} more line(s)")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="files or directories to scan (default: the whole repo)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite files in place instead of only reporting",
    )
    args = parser.parse_args()

    roots = [pathlib.Path(p) for p in (args.paths or ["."])]
    total = 0
    for root in roots:
        total += scan(root, args.fix)

    if total == 0:
        print("No encoding corruption detected.")
        return 0
    print(f"\n{total} corrupted line(s) {'repaired' if args.fix else 'found'}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
