"""Factory registry for SFLO run-directory isolation.

Each factory is one pipeline run with its own state directory:
`<sflo-parent>/<factory-name>/`. The registry at
`<sflo-parent>/registry.json` tracks names, status, PID, prompt snippet, and
the absolute run directory.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from typing import Optional


_SLUG_MAX_LEN = 40
_LEGACY_RESUME_SLUG_MAX_LEN = 128
_VALID_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "build",
    "create",
    "for",
    "from",
    "in",
    "make",
    "of",
    "on",
    "please",
    "the",
    "to",
    "with",
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _event(kind: str, **fields) -> dict:
    data = {"kind": kind, "at": _now_iso()}
    data.update({key: value for key, value in fields.items() if value is not None})
    return data


def validate_factory_name(name: str) -> bool:
    """Return True when `name` is a safe factory slug."""
    return (
        isinstance(name, str)
        and 2 <= len(name) <= _SLUG_MAX_LEN
        and bool(_VALID_SLUG_RE.match(name))
    )


def validate_legacy_resume_name(name: str) -> bool:
    """Return True when `name` is safe to resolve as an existing state dir."""
    return (
        isinstance(name, str)
        and 2 <= len(name) <= _LEGACY_RESUME_SLUG_MAX_LEN
        and bool(_VALID_SLUG_RE.match(name))
    )


def slug_from_prompt(prompt: str) -> str:
    """Build a stable factory slug from the first non-empty prompt line."""
    first_line = next((line.strip() for line in (prompt or "").splitlines() if line.strip()), "")
    tokens = re.findall(r"[A-Za-z0-9]+", first_line[:200].lower())
    words = [token for token in tokens if token not in _STOPWORDS] or tokens
    slug = "-".join(words[:6])
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug


class FactoryError(Exception):
    """Factory name or registry operation failed."""


class FactoryRegistry:
    """Registry of named SFLO factory runs under one parent directory."""

    STATUS_ACTIVE = "active"
    STATUS_DONE = "done"
    STATUS_STALE = "stale"
    STATUS_ABORTED = "aborted"

    _LEGACY_FILES = frozenset(
        {
            "state.json",
            "runner.pid",
            "pipeline.lock",
            "pipeline.log",
            "history.jsonl",
            "audit.log",
            "SCOPE.md",
            "BUILD-STATUS.md",
            "QA-REPORT.md",
            "SECURITY-REPORT.md",
            "PM-VERIFY.md",
            "SHIP-DECISION.md",
        }
    )
    _LEGACY_DIRS = frozenset({"logs", "interrogation"})

    def __init__(self, sflo_parent: str):
        self.parent = os.path.abspath(sflo_parent)
        self.path = os.path.join(self.parent, "registry.json")

    def _load(self) -> dict:
        if not os.path.isfile(self.path):
            return {"version": 1, "factories": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("factories"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "factories": {}}

    def _save(self, data: dict) -> None:
        os.makedirs(self.parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError, PermissionError):
            return False

    def refresh_statuses(self) -> None:
        """Mark active factories with dead PIDs as stale."""
        data = self._load()
        changed = False
        for entry in data["factories"].values():
            if entry.get("status") != self.STATUS_ACTIVE:
                continue
            pid = int(entry.get("pid", 0) or 0)
            if not self._pid_alive(pid):
                entry["status"] = self.STATUS_STALE
                entry["last_active"] = _now_iso()
                changed = True
        if changed:
            self._save(data)

    def get(self, name: str) -> Optional[dict]:
        return self._load()["factories"].get(name)

    def list_all(self) -> dict:
        self.refresh_statuses()
        return self._load()["factories"]

    def _has_state_dir(self, name: str) -> bool:
        return os.path.isfile(os.path.join(self.parent, name, "state.json"))

    def resolve_name(self, proposed: str, *, is_explicit: bool, is_resume: bool) -> str:
        """Resolve a requested or auto-generated factory name."""
        is_legacy_resume = (
            is_resume
            and validate_legacy_resume_name(proposed)
            and self._has_state_dir(proposed)
        )
        if not validate_factory_name(proposed) and not is_legacy_resume:
            raise FactoryError(
                f"Invalid factory name {proposed!r}. Use 2-40 lowercase "
                "letters, numbers, and single hyphens."
            )

        self.refresh_statuses()
        factories = self._load()["factories"]
        existing = factories.get(proposed)

        if is_resume:
            if not existing:
                if self._has_state_dir(proposed):
                    return proposed
                raise FactoryError(f"Cannot resume missing factory {proposed!r}.")
            return proposed

        if not existing:
            return proposed

        if is_explicit:
            raise FactoryError(
                f"Factory {proposed!r} already exists "
                f"(status={existing.get('status', '?')}). Resume it or choose a new name."
            )

        i = 2
        while f"{proposed}-{i}" in factories:
            i += 1
        return f"{proposed}-{i}"

    def register_start(self, name: str, sflo_dir: str, prompt: str, pid: int) -> None:
        data = self._load()
        previous = data["factories"].get(name, {})
        snippet = (prompt or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        data["factories"][name] = {
            "created": previous.get("created") or _now_iso(),
            "last_active": _now_iso(),
            "status": self.STATUS_ACTIVE,
            "pid": int(pid),
            "prompt_snippet": snippet,
            "sflo_dir": os.path.abspath(sflo_dir),
        }
        self._save(data)

    def register_end(
        self,
        name: str,
        status: str = STATUS_DONE,
        *,
        exit_kind: Optional[str] = None,
        exit_details: Optional[dict] = None,
    ) -> None:
        data = self._load()
        if name not in data["factories"]:
            return
        entry = data["factories"][name]
        entry["status"] = status
        entry["last_active"] = _now_iso()
        if exit_kind:
            details = dict(exit_details or {})
            details.setdefault("pid", int(entry.get("pid", 0) or 0))
            entry["last_exit"] = _event(exit_kind, **details)
        self._save(data)

    def kill(self, name: str, reason: str = "operator_kill") -> bool:
        data = self._load()
        if name not in data["factories"]:
            return False
        entry = data["factories"][name]
        entry["status"] = self.STATUS_ABORTED
        entry["last_active"] = _now_iso()
        entry["last_operator_action"] = _event("kill", reason=reason)
        entry["last_exit"] = _event(
            "operator_kill",
            reason=reason,
            observed=False,
            pid=int(entry.get("pid", 0) or 0),
        )
        sflo_dir = entry.get("sflo_dir")
        if sflo_dir:
            for lock_name in ("runner.pid", "pipeline.lock"):
                try:
                    os.unlink(os.path.join(sflo_dir, lock_name))
                except OSError:
                    pass
        self._save(data)
        return True

    def clean_stale(self) -> list[str]:
        self.refresh_statuses()
        data = self._load()
        removed = []
        for name, entry in list(data["factories"].items()):
            if entry.get("status") in (self.STATUS_STALE, self.STATUS_ABORTED):
                removed.append(name)
                del data["factories"][name]
        if removed:
            self._save(data)
        return removed

    def migrate_legacy(self) -> Optional[str]:
        """Move old top-level `.sflo` state files into a `legacy` factory."""
        if not os.path.isdir(self.parent):
            return None

        entries = set(os.listdir(self.parent))
        legacy_items = (entries & self._LEGACY_FILES) | {
            item
            for item in entries
            if item.endswith("-FEEDBACK.md")
            and os.path.isfile(os.path.join(self.parent, item))
        } | {
            item
            for item in (entries & self._LEGACY_DIRS)
            if os.path.isdir(os.path.join(self.parent, item))
        }
        if not legacy_items:
            return None

        name = "legacy"
        i = 2
        while os.path.exists(os.path.join(self.parent, name)):
            name = f"legacy-{i}"
            i += 1

        target = os.path.join(self.parent, name)
        os.makedirs(target, exist_ok=True)
        for item in legacy_items:
            try:
                shutil.move(os.path.join(self.parent, item), os.path.join(target, item))
            except (OSError, shutil.Error):
                pass

        data = self._load()
        data["factories"][name] = {
            "created": _now_iso(),
            "last_active": _now_iso(),
            "status": self.STATUS_DONE,
            "pid": 0,
            "prompt_snippet": "<migrated from legacy .sflo layout>",
            "sflo_dir": os.path.abspath(target),
        }
        self._save(data)
        return name


def final_status_from_pipeline_state(current_state: str) -> str:
    """Map pipeline terminal state to registry status."""
    if current_state == "done" or not current_state:
        return FactoryRegistry.STATUS_DONE
    return FactoryRegistry.STATUS_ABORTED


def format_registry_table(factories: dict) -> str:
    """Render registry rows as a compact plain-text table."""
    if not factories:
        return "No factories registered."

    rows = [("NAME", "STATUS", "PID", "LAST ACTIVE", "PROMPT")]
    for name, entry in sorted(
        factories.items(),
        key=lambda item: item[1].get("last_active", ""),
        reverse=True,
    ):
        rows.append(
            (
                name,
                entry.get("status", "?") or "?",
                str(entry.get("pid", "-") or "-"),
                (entry.get("last_active", "-") or "-")[:19].replace("T", " "),
                (entry.get("prompt_snippet", "-") or "-")[:60],
            )
        )
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(str(value).ljust(widths[j]) for j, value in enumerate(row)))
        if i == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)
