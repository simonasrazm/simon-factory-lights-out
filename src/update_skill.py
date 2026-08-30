#!/usr/bin/env python3
"""Update an installed SFLO skill through a disposable source checkout."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_REPO = "https://github.com/simonasrazm/simon-factory-lights-out.git"
MANIFEST_NAME = ".sflo-install.json"


def update_skill(
    installed_root: Path,
    *,
    source: Path | None = None,
    repository: str = DEFAULT_REPO,
    branch: str = "main",
) -> None:
    installed_root = installed_root.expanduser().resolve()
    manifest_path = installed_root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"installed SFLO manifest is unreadable: {exc}") from exc
    if manifest.get("product") != "sflo" or not manifest.get("runtime"):
        raise RuntimeError("installed SFLO manifest has no valid runtime ownership")

    temporary_root: Path | None = None
    if source is None:
        temporary_root = Path(tempfile.mkdtemp(prefix="sflo-update-"))
        source = temporary_root / "source"
        completed = subprocess.run(
            ["git", "clone", "--branch", branch, "--depth", "1", repository, str(source)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise RuntimeError(f"could not download SFLO update: {detail}")
    else:
        source = source.expanduser().resolve()

    try:
        installer = source / "src" / "install_skill.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(installer),
                "--source",
                str(source),
                "--runtime",
                str(manifest["runtime"]),
                "--destination",
                str(installed_root),
                "--provenance",
                repository if temporary_root else str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"SFLO update activation failed: {detail}")
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", type=Path, help="local SFLO source (testing/offline use)")
    parser.add_argument("--repository", default=DEFAULT_REPO)
    parser.add_argument("--branch", default="main")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        update_skill(
            args.installed_root,
            source=args.source,
            repository=args.repository,
            branch=args.branch,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"SFLO updated at {args.installed_root.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
