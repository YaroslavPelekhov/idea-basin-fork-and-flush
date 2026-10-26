#!/usr/bin/env python3
"""Install this skill for Codex and/or Claude Code without changing app settings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import tempfile


NAME = "idea-basin-fork-and-flush"
SOURCE = Path(__file__).resolve().parents[1]


def digest(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def install(source: Path, root: Path, replace: bool = False) -> str:
    destination = root.expanduser() / NAME
    if not (source / "SKILL.md").is_file():
        raise ValueError("source does not contain SKILL.md")
    if destination.is_symlink():
        raise ValueError(f"refusing to replace symlink: {destination}")
    if destination.exists():
        if destination.is_dir() and digest(source) == digest(destination):
            return f"Already current: {destination}"
        if not replace:
            raise ValueError(f"destination exists: {destination}; use --replace to back it up")
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=f".{NAME}-", dir=destination.parent) as temporary:
        staged = Path(temporary) / NAME
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if digest(source) != digest(staged):
            raise ValueError("staged copy verification failed")
        if destination.exists():
            backup_root = destination.parent.parent / "skill-backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = backup_root / f"{NAME}-{stamp}"
            destination.rename(backup)
        try:
            staged.rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
    suffix = f" (backup: {backup})" if backup else ""
    return f"Installed: {destination}{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--codex-dir", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude" / "skills")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    roots = []
    if args.target in {"codex", "both"}:
        roots.append(args.codex_dir)
    if args.target in {"claude", "both"}:
        roots.append(args.claude_dir)
    try:
        for root in dict.fromkeys(roots):
            print(install(SOURCE, root, args.replace))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
