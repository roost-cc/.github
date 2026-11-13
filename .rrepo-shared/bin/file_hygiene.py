#!/usr/bin/env python3
"""Generic file checks for the shared-config `file-hygiene` and `large-files` hooks.

`file-hygiene` (the default) runs on the registered file types only (the hook's
`types_or:` in .pre-commit-hooks.yaml). Each file that holds no NUL byte gets:
  - trailing whitespace (auto-fixed; Markdown keeps two-space hard line breaks)
  - exactly one newline at end of file (auto-fixed)
  - leftover merge-conflict markers
  - YAML syntax — *.yaml / *.yml (requires PyYAML)
  - JSON syntax — *.json

`large-files` (`--size-only`) lists the staged files itself and checks every
regular one, so a repo's top-level `exclude:` cannot skip it. It refuses a file
over --max-kb (default 500) unless the same path is already over the limit at
HEAD. With no HEAD, every file over the limit is refused.

Exit code is non-zero when any problem is found, including problems that were
auto-fixed (pre-commit convention: a fixing hook fails so the user re-stages).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

MARKDOWN_EXTS = {".md", ".markdown"}
YAML_EXTS = {".yaml", ".yml"}
# Same patterns as pre-commit-hooks' check-merge-conflict: the ours/theirs
# markers are always followed by a ref name, the separator is a bare line.
CONFLICT_MARKER_PREFIXES = (b"<<<<<<< ", b"======= ", b">>>>>>> ")
CONFLICT_SEPARATOR = b"======="


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def head_size_kb(path: str) -> float | None:
    """The size of `path` at HEAD in kB, or None when HEAD has no such file (or no HEAD).

    git's stderr is discarded, not decoded: it holds the file name, which need not be UTF-8.
    """
    result = subprocess.run(
        ["git", "cat-file", "-s", f"HEAD:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    return int(result.stdout) / 1024 if result.returncode == 0 else None


def staged_files() -> list[str]:
    """Every staged path that is a regular file: no deletion, symlink or submodule.

    `-z` keeps each name as git stores it; os.fsdecode keeps a non-UTF-8 name intact.
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=d"], stdout=subprocess.PIPE, check=True
    )
    paths = [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]
    return [path for path in paths if os.path.isfile(path) and not os.path.islink(path)]


def check_large(path: str, max_kb: int, problems: list[str]) -> None:
    size_kb = os.path.getsize(path) / 1024
    if size_kb <= max_kb:
        return
    head_kb = head_size_kb(path)
    if head_kb is not None and head_kb > max_kb:
        return
    problems.append(
        f"{path}: file is {size_kb:.0f} kB, larger than the {max_kb} kB limit "
        "(only a file already over the limit at HEAD may change)"
    )


def fix_trailing_whitespace(path: str, data: bytes, problems: list[str]) -> bytes:
    is_markdown = os.path.splitext(path)[1].lower() in MARKDOWN_EXTS
    fixed_lines = []
    changed = False
    for line in data.splitlines(keepends=True):
        content = line.rstrip(b"\r\n")
        ending = line[len(content) :]
        stripped = content.rstrip(b" \t")
        if is_markdown and content != stripped and content.endswith(b"  ") and b"\t" not in content[len(stripped) :]:
            # Preserve Markdown hard line breaks, normalized to exactly two spaces.
            stripped += b"  "
        if content != stripped:
            changed = True
        fixed_lines.append(stripped + ending)
    if changed:
        problems.append(f"{path}: trailing whitespace (fixed)")
        return b"".join(fixed_lines)
    return data


def fix_end_of_file(path: str, data: bytes, problems: list[str]) -> bytes:
    if not data:
        return data
    fixed = data.rstrip(b"\r\n \t")
    if not fixed:
        problems.append(f"{path}: file contains only whitespace (fixed to empty file)")
        return b""
    fixed += b"\r\n" if data.splitlines(keepends=True)[0].endswith(b"\r\n") else b"\n"
    if fixed != data:
        problems.append(f"{path}: missing or extra newline at end of file (fixed)")
    return fixed


def check_merge_conflict(path: str, data: bytes, problems: list[str]) -> None:
    for lineno, line in enumerate(data.splitlines(), start=1):
        if line == CONFLICT_SEPARATOR or any(line.startswith(marker) for marker in CONFLICT_MARKER_PREFIXES):
            problems.append(f"{path}:{lineno}: merge conflict marker '{line[:7].decode()}'")


def check_yaml(path: str, data: bytes, problems: list[str]) -> None:
    try:
        import yaml
    except ImportError:
        problems.append(
            f"{path}: cannot validate YAML — PyYAML is not installed for python3 "
            "(install it, e.g. 'python3 -m pip install pyyaml' or 'apt install python3-yaml')"
        )
        return
    try:
        list(yaml.safe_load_all(data))
    except yaml.YAMLError as exc:
        problems.append(f"{path}: invalid YAML: {exc}")


def check_json(path: str, data: bytes, problems: list[str]) -> None:
    try:
        json.loads(data)
    except ValueError as exc:
        problems.append(f"{path}: invalid JSON: {exc}")


def process(path: str, problems: list[str]) -> None:
    with open(path, "rb") as fh:
        data = fh.read()
    if is_binary(data):
        return

    fixed = fix_trailing_whitespace(path, data, problems)
    fixed = fix_end_of_file(path, fixed, problems)
    if fixed != data:
        with open(path, "wb") as fh:
            fh.write(fixed)

    check_merge_conflict(path, fixed, problems)
    ext = os.path.splitext(path)[1].lower()
    if ext in YAML_EXTS:
        check_yaml(path, fixed, problems)
    elif ext == ".json":
        check_json(path, fixed, problems)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-kb", type=int, default=500, help="large-file limit in kB (default: 500)")
    parser.add_argument(
        "--size-only", action="store_true", help="check only the size of every staged file (the large-files hook)"
    )
    parser.add_argument("filenames", nargs="*", help="files to check (provided by pre-commit; not with --size-only)")
    args = parser.parse_args(argv)

    problems: list[str] = []
    if args.size_only:
        for path in staged_files():
            check_large(path, args.max_kb, problems)
    else:
        for path in args.filenames:
            process(path, problems)

    # A file name need not be UTF-8: write its original bytes back, whatever the locale.
    sys.stdout.reconfigure(errors="surrogateescape")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
