"""SFLO pipeline.yaml config loader — hand-rolled YAML parser, no external deps."""

import os


# Built-in defaults (identical to original hardcoded values)
_DEFAULTS = {
    "threshold": "B+",
    "guardian": {
        "enabled": False,
        "max_spawns": 50,
        "wall_clock_s": 7200,
        "circuit_breaker_window": 5,
    },
    "gates": {
        1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
        2: {"artifact": "BUILD-STATUS.md", "role": "dev", "gate_doc": "gates/build.md"},
        3: {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
        4: {"artifact": "PM-VERIFY.md", "role": "pm", "gate_doc": "gates/verify.md"},
        5: {"artifact": "SHIP-DECISION.md", "role": "sflo", "gate_doc": "gates/ship.md"},
    },
}

_GRADE_MAP = {"A": 6, "A-": 5.5, "B+": 5, "B": 4, "B-": 3.5, "C": 3, "D": 2, "F": 1}


def _parse_gate_key(s):
    """Parse a gate key string — returns float (e.g. '1' -> 1.0, '1.5' -> 1.5).
    Returns None if not a valid number."""
    try:
        val = float(s)
        # Return int if it's a whole number, float otherwise
        return int(val) if val == int(val) else val
    except (ValueError, TypeError):
        return None


def _strip_inline_comment(s):
    """Strip inline comment from a value string (e.g. 'A  # comment' -> 'A')."""
    # Only strip if ' #' appears — protect against # in paths
    idx = s.find(" #")
    if idx != -1:
        return s[:idx].strip()
    return s.strip()


def _parse_bool(s):
    """Parse a boolean string."""
    return s.strip().lower() in ("true", "yes", "1")


def parse_pipeline_yaml(path):
    """Parse pipeline.yaml — supports threshold, guardian, and gates sections.

    Supported subset:
    - Top-level keys: threshold, guardian, gates
    - Nested dicts (guardian settings): 4-space indent
    - Gate entries: 2-space indent under gates:, then 4-space for fields
    - Gate keys as numbers (integers or floats like 1.5)
    - Comments (#) and blank lines are skipped
    - Tabs in indentation are rejected

    Returns (config_dict, error_string). config_dict is None on error.
    """
    if not os.path.isfile(path):
        return None, f"File not found: {path}"

    result = {}
    current_section = None  # "threshold", "guardian", "gates"
    current_gate_key = None  # numeric gate key (int or float)
    errors = []

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            raw = line.rstrip("\n\r")
            content = raw.strip()

            # Skip blank lines and comments
            if not content or content.startswith("#"):
                continue

            # Reject tabs in indentation
            leading = raw[: len(raw) - len(raw.lstrip())]
            if "\t" in leading:
                errors.append(f"Line {line_num}: tabs in indentation not supported")
                continue

            indent = len(leading)

            if indent == 0:
                # Top-level key
                if ":" in content:
                    key, _, val = content.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if key == "threshold":
                        result["threshold"] = _strip_inline_comment(val) if val else "B+"
                        current_section = "threshold"
                        current_gate_key = None
                    elif key == "guardian":
                        result["guardian"] = {}
                        current_section = "guardian"
                        current_gate_key = None
                    elif key == "gates":
                        result["gates"] = {}
                        current_section = "gates"
                        current_gate_key = None
                    else:
                        current_section = None
                        current_gate_key = None
                continue

            if current_section == "guardian" and indent == 2:
                # guardian sub-key
                if ":" in content:
                    key, _, val = content.partition(":")
                    key = key.strip()
                    val = val.strip()
                    val = _strip_inline_comment(val)
                    if key == "enabled":
                        result["guardian"]["enabled"] = _parse_bool(val)
                    elif key in ("max_spawns", "wall_clock_s", "circuit_breaker_window"):
                        try:
                            result["guardian"][key] = int(val)
                        except ValueError:
                            errors.append(f"Line {line_num}: invalid integer for {key}: {val}")
                    else:
                        result["guardian"][key] = val
                continue

            if current_section == "gates":
                if indent == 2:
                    # Gate key line: "  1:" or "  1.5:"
                    if content.endswith(":"):
                        gate_str = content[:-1].strip()
                        gate_key = _parse_gate_key(gate_str)
                        if gate_key is not None:
                            current_gate_key = gate_key
                            result["gates"][gate_key] = {}
                        else:
                            errors.append(f"Line {line_num}: invalid gate key: {gate_str!r}")
                            current_gate_key = None
                    continue

                if indent == 4 and current_gate_key is not None:
                    # Gate field: "    artifact: SCOPE.md"
                    if ":" in content:
                        key, _, val = content.partition(":")
                        key = key.strip()
                        val = _strip_inline_comment(val)
                        result["gates"][current_gate_key][key] = val
                    continue

    if errors:
        return None, f"Parse errors in {path}: {'; '.join(errors)}"

    return result, None


def resolve_pipeline_path(explicit=None):
    """Resolve pipeline.yaml: explicit -> cwd -> sflo/ subdir -> SFLO_ROOT."""
    from .constants import SFLO_ROOT

    if explicit and os.path.isfile(explicit):
        return explicit
    cwd_path = os.path.join(os.getcwd(), "pipeline.yaml")
    if os.path.isfile(cwd_path):
        return cwd_path
    sflo_path = os.path.join(os.getcwd(), "sflo", "pipeline.yaml")
    if os.path.isfile(sflo_path):
        return sflo_path
    # Fallback: built-in default in SFLO_ROOT
    root_path = os.path.join(SFLO_ROOT, "pipeline.yaml")
    if os.path.isfile(root_path):
        return root_path
    return None


def load_pipeline_config(path=None):
    """Load and merge pipeline config. Falls back to built-in defaults.

    Returns a dict with keys:
      - gates: dict mapping numeric key -> {artifact, role, gate_doc}
      - grade_threshold: numeric threshold value
      - guardian: dict with guardian settings
    """
    resolved = path or resolve_pipeline_path()

    raw = {}
    if resolved:
        parsed, err = parse_pipeline_yaml(resolved)
        if parsed is not None:
            raw = parsed
        # If parse fails, raw stays empty and we fall through to defaults

    # Merge with defaults
    threshold_str = raw.get("threshold", _DEFAULTS["threshold"])
    grade_threshold = _GRADE_MAP.get(threshold_str, _DEFAULTS["threshold"])
    if not isinstance(grade_threshold, (int, float)):
        # Unknown grade string — fall back to default
        grade_threshold = _GRADE_MAP[_DEFAULTS["threshold"]]

    guardian_raw = raw.get("guardian", {})
    guardian = {**_DEFAULTS["guardian"], **guardian_raw}

    gates_raw = raw.get("gates", {})
    if gates_raw:
        # Sort gates numerically
        gates = {k: gates_raw[k] for k in sorted(gates_raw.keys())}
    else:
        gates = dict(_DEFAULTS["gates"])

    return {
        "gates": gates,
        "grade_threshold": grade_threshold,
        "guardian": guardian,
    }
