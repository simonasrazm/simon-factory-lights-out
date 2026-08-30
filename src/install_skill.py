#!/usr/bin/env python3
"""Install a self-contained, ownership-marked SFLO runtime skill.

The installer copies only runtime assets, validates them in a sibling staging
directory, and activates the staged directory with a rollback-safe rename.
It deliberately does not mutate project ``pipeline.yaml`` or ``.sflo`` state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


MANIFEST_NAME = ".sflo-install.json"
MANIFEST_SCHEMA = 1
PRODUCT = "sflo"
RUNTIMES = ("codex", "cursor", "claude-code", "openclaw")
IGNORED_NAMES = {
    ".DS_Store",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".sflo",
    "__pycache__",
}


class InstallError(RuntimeError):
    """Raised when a skill cannot be installed without risking user files."""


@dataclass(frozen=True)
class InstallResult:
    destination: Path
    runtime: str
    version: str
    manifest: Path


def install_skill(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    runtime: str,
    version: str = "development",
    provenance: str | None = None,
) -> InstallResult:
    """Install ``source`` as a complete SFLO skill at ``destination``.

    Existing destinations are replaced only when they carry an SFLO ownership
    marker. All copying and initial verification happen in a sibling staging
    directory; a failed activation restores the previous owned installation.
    """

    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if runtime not in RUNTIMES:
        raise InstallError(
            f"unsupported runtime {runtime!r}; expected one of: {', '.join(RUNTIMES)}"
        )
    _validate_source(source_path)
    if destination_path.exists() and not _is_owned(destination_path):
        raise InstallError(
            f"destination exists and is not SFLO-owned: {destination_path}"
        )

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.name}.staging-",
            dir=destination_path.parent,
        )
    )
    backup = destination_path.parent / (
        f".{destination_path.name}.backup-{uuid.uuid4().hex}"
    )
    activated = False
    backed_up = False
    try:
        _build_payload(
            source_path,
            stage,
            final_destination=destination_path,
            runtime=runtime,
            version=version,
            provenance=provenance or str(source_path),
        )
        _verify_payload(stage)

        if destination_path.exists():
            os.replace(destination_path, backup)
            backed_up = True
        os.replace(stage, destination_path)
        activated = True
        try:
            _verify_payload(destination_path)
        except Exception as exc:
            _remove_path(destination_path)
            activated = False
            if backed_up:
                os.replace(backup, destination_path)
                backed_up = False
            raise InstallError(f"activated skill verification failed: {exc}") from exc

        if backed_up:
            _remove_path(backup)
            backed_up = False
    except InstallError:
        raise
    except Exception as exc:
        if activated:
            _remove_path(destination_path)
        if backed_up and backup.exists():
            os.replace(backup, destination_path)
        raise InstallError(f"SFLO skill installation failed: {exc}") from exc
    finally:
        _remove_path(stage)
        if backup.exists() and destination_path.exists():
            _remove_path(backup)

    return InstallResult(
        destination=destination_path,
        runtime=runtime,
        version=version,
        manifest=destination_path / MANIFEST_NAME,
    )


def _validate_source(source: Path) -> None:
    required = (
        source / "skill" / "SKILL.md",
        source / "src" / "runner.py",
        source / "src" / "scaffold.py",
        source / "agents",
        source / "gates",
        source / "pipeline.yaml",
        source
        / "vendor"
        / "mattpocock-skills"
        / "skills"
        / "engineering"
        / "tdd"
        / "SKILL.md",
    )
    missing = [str(path.relative_to(source)) for path in required if not path.exists()]
    if missing:
        raise InstallError("source is not a complete SFLO repository; missing: " + ", ".join(missing))


def _build_payload(
    source: Path,
    stage: Path,
    *,
    final_destination: Path,
    runtime: str,
    version: str,
    provenance: str,
) -> None:
    shutil.copy2(source / "skill" / "SKILL.md", stage / "SKILL.md")
    for directory in ("src", "agents", "gates"):
        shutil.copytree(
            source / directory,
            stage / directory,
            dirs_exist_ok=True,
            ignore=_ignore_runtime_noise,
        )
    matt_source = source / "vendor" / "mattpocock-skills"
    matt_destination = stage / "vendor" / "mattpocock-skills"
    shutil.copytree(
        matt_source / "skills",
        matt_destination / "skills",
        dirs_exist_ok=True,
        ignore=_ignore_runtime_noise,
    )
    for vendor_metadata in ("LICENSE", "SFLO-VENDOR.md"):
        metadata = matt_source / vendor_metadata
        if metadata.is_file():
            matt_destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(metadata, matt_destination / vendor_metadata)
    for pipeline in source.glob("pipeline*.yaml"):
        if pipeline.is_file():
            shutil.copy2(pipeline, stage / pipeline.name)
    for optional in ("LICENSE",):
        path = source / optional
        if path.is_file():
            shutil.copy2(path, stage / optional)

    _prune_empty_directories(stage)
    _render_skill(stage / "SKILL.md", final_destination, runtime)
    files = _hash_files(stage)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "product": PRODUCT,
        "runtime": runtime,
        "version": version,
        "provenance": provenance,
        "files": files,
    }
    (stage / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_skill(skill_file: Path, destination: Path, runtime: str) -> None:
    content = skill_file.read_text(encoding="utf-8")
    replacements = {
        "{{SFLO_PATH}}": str(destination),
        "{{SFLO_RUNNER_SH}}": str(destination / "src" / "runner.py"),
        "{{SFLO_SCAFFOLD_SH}}": str(destination / "src" / "scaffold.py"),
        "{{SFLO_CURSOR_STOP_HOOK_SH}}": str(
            destination / "src" / "hooks" / "cursor" / "stop_hook.py"
        ),
        "{{SFLO_RUNTIME}}": runtime,
    }
    for marker, value in replacements.items():
        content = content.replace(marker, value)
    skill_file.write_text(content, encoding="utf-8")


def _ignore_runtime_noise(_directory: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name in IGNORED_NAMES
        or name.endswith((".pyc", ".pyo"))
        or name == "tests"
    }
    return ignored


def _prune_empty_directories(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _hash_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != MANIFEST_NAME:
            relative = path.relative_to(root).as_posix()
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _is_owned(destination: Path) -> bool:
    manifest_path = destination / MANIFEST_NAME
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return manifest.get("product") == PRODUCT
    marker = destination / ".sflo-owned"
    return marker.is_file() and marker.read_text(encoding="utf-8").strip() == PRODUCT


def _verify_payload(root: Path) -> None:
    manifest_path = root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise InstallError(f"invalid ownership manifest: {exc}") from exc
    if manifest.get("product") != PRODUCT or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise InstallError("invalid SFLO ownership manifest")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or expected != _hash_files(root):
        raise InstallError("installed payload does not match its ownership manifest")
    runner = root / "src" / "runner.py"
    completed = subprocess.run(
        [sys.executable, str(runner), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise InstallError(f"runner smoke test failed: {detail}")


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SFLO source repository")
    parser.add_argument("--runtime", required=True, choices=RUNTIMES)
    parser.add_argument("--destination", required=True, help="canonical sflo skill directory")
    parser.add_argument("--version", default="development")
    parser.add_argument("--provenance")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = install_skill(
            args.source,
            args.destination,
            runtime=args.runtime,
            version=args.version,
            provenance=args.provenance,
        )
    except InstallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "destination": str(result.destination),
                "runtime": result.runtime,
                "version": result.version,
                "manifest": str(result.manifest),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
