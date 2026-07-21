"""Tests for factory registry resume compatibility."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


_SFLO_DIR = Path(__file__).parent.parent.parent
if str(_SFLO_DIR) not in sys.path:
    sys.path.insert(0, str(_SFLO_DIR))


from src.factory_registry import FactoryError, FactoryRegistry  # noqa: E402


def _write_state(parent: Path, name: str) -> None:
    factory_dir = parent / name
    factory_dir.mkdir()
    (factory_dir / "state.json").write_text(
        json.dumps({"current_state": "gate-5", "prompt": "resume me"}),
        encoding="utf-8",
    )


def test_resume_unregistered_legacy_state_dir(tmp_path):
    _write_state(tmp_path, "viewer-fixes-v4")

    registry = FactoryRegistry(str(tmp_path))

    assert registry.resolve_name(
        "viewer-fixes-v4", is_explicit=True, is_resume=True
    ) == "viewer-fixes-v4"


def test_resume_long_legacy_state_dir(tmp_path):
    name = "stst-generator-read-project-test-config-2"
    assert len(name) > 40
    _write_state(tmp_path, name)

    registry = FactoryRegistry(str(tmp_path))

    assert registry.resolve_name(
        name, is_explicit=True, is_resume=True
    ) == name


def test_new_factory_still_rejects_long_slug(tmp_path):
    name = "stst-generator-read-project-test-config-2"
    _write_state(tmp_path, name)

    registry = FactoryRegistry(str(tmp_path))

    with pytest.raises(FactoryError):
        registry.resolve_name(name, is_explicit=True, is_resume=False)


def test_resume_missing_factory_still_rejected(tmp_path):
    registry = FactoryRegistry(str(tmp_path))

    with pytest.raises(FactoryError):
        registry.resolve_name(
            "stst-generator-read-project-test-config-2",
            is_explicit=True,
            is_resume=True,
        )
