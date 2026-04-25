"""sflo eval registry — plugin discovery, loading, and filtering.

Plugins are discovered from the `evals:` section of bindings.yaml, loaded by
importlib at runner startup, and stored in a module-level list. Adapters call
registered_evals_for_site() to get the filtered set for a given hook site.

Fail-safe design: any per-plugin load failure logs a warning and continues —
other plugins still register. An empty `evals:` section (or no section at all)
is a no-op with zero overhead.
"""

from __future__ import annotations

import importlib
import warnings
from pathlib import Path
from typing import List, Optional

from .base import HookSite, SfloEval

# ---------------------------------------------------------------------------
# EvalRegistry class — encapsulates the loaded evals list.
# Module-level _LOADED_EVALS is the registry's internal list so that existing
# callers (and tests) that import _LOADED_EVALS directly continue to work.
# ---------------------------------------------------------------------------


class EvalRegistry:
    """Encapsulates the loaded SfloEval plugin list.

    Using a class instead of a bare module-global makes the storage explicit
    and testable. The module-level ``_LOADED_EVALS`` list IS this registry's
    backing store — they are the same object, so direct references stay valid.
    """

    def __init__(self, store: List[SfloEval]) -> None:
        self._store = store

    def clear(self) -> None:
        self._store.clear()

    def extend(self, items: List[SfloEval]) -> None:
        self._store.extend(items)

    def __iter__(self):
        return iter(self._store)

    def __len__(self):
        return len(self._store)


_LOADED_EVALS: List[SfloEval] = []
_registry = EvalRegistry(_LOADED_EVALS)


def clear_registry() -> None:
    """Reset the registry. Used between tests to ensure isolation."""
    _registry.clear()


# ---------------------------------------------------------------------------
# Public loader — called by runner.py at pipeline startup
# ---------------------------------------------------------------------------


def load_evals_from_bindings(bindings_path: Path) -> List[SfloEval]:
    """Read `evals:` section from bindings.yaml and load + register plugins.

    For each entry:
      - importlib.import_module(entry['module'])
      - getattr(mod, entry['class'])
      - instantiate with config dict
      - validate it's a SfloEval subclass; skip + warn otherwise

    Returns sorted list (by priority, then registration order).
    If `evals:` absent → returns []. If a single plugin fails to load,
    log warning + continue (other plugins still register).
    """
    # Clear in place — do NOT reassign the list object so that callers
    # holding a direct reference to _LOADED_EVALS see the updated state.
    _registry.clear()

    if not bindings_path:
        return []

    bp = Path(bindings_path)
    if not bp.is_file():
        return []

    entries = _load_evals_section(bp)
    if not entries:
        return []

    loaded: List[SfloEval] = []

    for i, entry in enumerate(entries):
        # Skip disabled entries
        if not entry.get("enabled", True):
            continue

        module_name = entry.get("module", "").strip()
        class_name = entry.get("class", "").strip()

        if not module_name or not class_name:
            warnings.warn(
                f"[sflo evals] entry {i}: missing 'module' or 'class' — skipping",
                stacklevel=2,
            )
            continue

        # Import module
        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            warnings.warn(
                f"[sflo evals] '{class_name}': cannot import '{module_name}': {exc} — skipping",
                stacklevel=2,
            )
            continue
        except Exception as exc:
            warnings.warn(
                f"[sflo evals] '{class_name}': error importing '{module_name}': {exc} — skipping",
                stacklevel=2,
            )
            continue

        # Get class
        cls = getattr(mod, class_name, None)
        if cls is None:
            warnings.warn(
                f"[sflo evals] class '{class_name}' not found in '{module_name}' — skipping",
                stacklevel=2,
            )
            continue

        if not (isinstance(cls, type) and issubclass(cls, SfloEval)):
            warnings.warn(
                f"[sflo evals] '{class_name}' is not a SfloEval subclass — skipping",
                stacklevel=2,
            )
            continue

        # Instantiate
        try:
            config = entry.get("config") or {}
            instance = cls(config=config)
            # Attach match filter (for registered_evals_for_site filtering)
            instance._match = entry.get("match") or {}  # type: ignore[attr-defined]
            # Effective priority: bindings override > class default
            entry_priority = entry.get("priority")
            if entry_priority is not None:
                instance._bindings_priority = int(entry_priority)  # type: ignore[attr-defined]
            else:
                instance._bindings_priority = cls.priority  # type: ignore[attr-defined]
            loaded.append(instance)
        except Exception as exc:
            warnings.warn(
                f"[sflo evals] failed to instantiate '{class_name}': {exc} — skipping",
                stacklevel=2,
            )
            continue

    # Stable sort by priority (lower = first), registration order breaks ties
    loaded.sort(key=lambda e: getattr(e, "_bindings_priority", 100))
    _registry.clear()
    _registry.extend(loaded)
    return list(_LOADED_EVALS)


# ---------------------------------------------------------------------------
# Query — called by adapters at hook sites
# ---------------------------------------------------------------------------


def registered_evals_for_site(
    site: HookSite,
    role: Optional[str] = None,
    gate: Optional[int] = None,
) -> List[SfloEval]:
    """Return loaded plugins filtered by site and optional role/gate.

    Filtering rules:
      1. site must be in eval_inst.__class__.sites
      2. If role provided AND eval has match.roles → role must be in that list
      3. If gate provided AND eval has match.gates → gate must be in that list
      4. No match block → passes all roles/gates
    """
    result = []
    for eval_inst in _registry:
        cls_sites = eval_inst.__class__.sites or []
        if site not in cls_sites:
            continue
        if not matches_filter(eval_inst, role=role, gate=gate):
            continue
        result.append(eval_inst)
    return result


def matches_filter(
    eval_inst: SfloEval,
    role: Optional[str] = None,
    gate: Optional[int] = None,
) -> bool:
    """Evaluate the plugin's match: config block.

    match:
      roles: [dev, qa]       # optional; omit = all roles pass
      gates: [2, 3]          # optional; omit = all gates pass

    Returns True if all conditions satisfied OR no match block defined.
    """
    match: dict = getattr(eval_inst, "_match", {}) or {}

    if not match:
        return True

    # Role filter
    allowed_roles = match.get("roles")
    if allowed_roles is not None and role is not None:
        if role not in allowed_roles:
            return False

    # Gate filter
    allowed_gates = match.get("gates")
    if allowed_gates is not None and gate is not None:
        if gate not in allowed_gates:
            return False

    return True


# ---------------------------------------------------------------------------
# bindings.yaml parser for the `evals:` section
# ---------------------------------------------------------------------------


def _load_evals_section(bindings_path: Path) -> List[dict]:
    """Parse the `evals:` section from bindings.yaml.

    Tries yaml.safe_load first (if PyYAML is available); falls back to a
    hand-rolled mini-parser for the specific evals: format so there's
    zero new external dependencies.
    """
    # Fast path: use PyYAML if available
    try:
        import yaml as _yaml  # type: ignore[import]

        with open(bindings_path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        result = data.get("evals") or []
        return result if isinstance(result, list) else []
    except ImportError:
        pass  # fall through to manual parser
    except Exception:
        return []

    # Fallback: hand-rolled parser for the specific evals: subset
    return _parse_evals_section_manual(bindings_path)


def _parse_value(v: str):
    """Parse a YAML scalar or inline-list value."""
    v = v.strip()
    if v == "true":
        return True
    if v == "false":
        return False
    if v in ("null", "~", ""):
        return None
    # Inline list: [a, b, c]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        items = [i.strip().strip('"').strip("'") for i in inner.split(",")]
        result = []
        for item in items:
            try:
                result.append(int(item))
            except ValueError:
                result.append(item)
        return result
    # Integer
    try:
        return int(v)
    except ValueError:
        pass
    # Quoted string
    if len(v) >= 2 and (
        (v.startswith('"') and v.endswith('"'))
        or (v.startswith("'") and v.endswith("'"))
    ):
        return v[1:-1]
    return v


def _parse_evals_section_manual(bindings_path: Path) -> List[dict]:
    """Minimal hand-rolled parser for the evals: section.

    Handles:
      - Top-level `evals:` key
      - List items at indent 2 starting with `- `
      - Flat key-value fields at indent 4
      - Nested dicts (match:, config:) at indent 4 with fields at indent 6
      - Inline lists [a, b, c] and bool/int scalars
    """
    try:
        with open(bindings_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return []

    evals: List[dict] = []
    in_evals = False
    current_item: Optional[dict] = None
    current_section: Optional[str] = None  # "match" | "config" | None

    for raw_line in lines:
        raw = raw_line.rstrip("\n\r")
        stripped = raw.strip()

        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip(" "))

        # Top-level key
        if indent == 0:
            if stripped == "evals:":
                in_evals = True
            else:
                if in_evals and current_item is not None:
                    evals.append(current_item)
                    current_item = None
                in_evals = False
            continue

        if not in_evals:
            continue

        # List item: `  - key: value` (indent 2, starts with "- ")
        if indent == 2 and stripped.startswith("- "):
            if current_item is not None:
                evals.append(current_item)
            current_item = {}
            current_section = None
            kv = stripped[2:].strip()
            if kv and ":" in kv:
                k, _, v = kv.partition(":")
                current_item[k.strip()] = _parse_value(v.strip())
            continue

        if current_item is None:
            continue

        # Nested section field at indent 6
        if indent >= 6 and current_section is not None:
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                current_item[current_section][k.strip()] = _parse_value(v.strip())
            continue

        # Item-level field at indent 4
        if indent >= 4:
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if not v:
                    # Section header (match:, config:)
                    current_section = k
                    current_item[current_section] = {}
                else:
                    # Flat field — exit any active nested section
                    current_section = None
                    current_item[k] = _parse_value(v)
            continue

    if in_evals and current_item is not None:
        evals.append(current_item)

    return evals
