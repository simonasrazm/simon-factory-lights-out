"""SFLO pipeline.yaml config loader — hand-rolled YAML parser, no external deps."""

import os


# Built-in defaults (identical to original hardcoded values)
_DEFAULTS = {
    "threshold": "B+",
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
        return int(val) if val == int(val) else val
    except (ValueError, TypeError):
        return None


def _strip_inline_comment(s):
    """Strip inline comment from a value string (e.g. 'A  # comment' -> 'A')."""
    idx = s.find(" #")
    if idx != -1:
        return s[:idx].strip()
    return s.strip()


def parse_pipeline_yaml(path):
    """Parse pipeline.yaml — supports threshold and gates sections.

    Supported subset:
    - Top-level keys: threshold, gates
    - Gate entries: 2-space indent under gates:, then 4-space for fields
    - Gate keys as numbers (integers or floats like 1.5)
    - Comments (#) and blank lines are skipped
    - Tabs in indentation are rejected
    - Unknown top-level keys are silently ignored

    Returns (config_dict, error_string). config_dict is None on error.
    """
    if not os.path.isfile(path):
        return None, f"File not found: {path}"

    result = {}
    current_section = None
    current_gate_key = None
    errors = []

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            raw = line.rstrip("\n\r")
            content = raw.strip()

            if not content or content.startswith("#"):
                continue

            leading = raw[: len(raw) - len(raw.lstrip())]
            if "\t" in leading:
                errors.append(f"Line {line_num}: tabs in indentation not supported")
                continue

            indent = len(leading)

            if indent == 0:
                if ":" in content:
                    key, _, val = content.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if key == "threshold":
                        result["threshold"] = _strip_inline_comment(val) if val else "B+"
                        current_section = "threshold"
                        current_gate_key = None
                    elif key == "gates":
                        result["gates"] = {}
                        current_section = "gates"
                        current_gate_key = None
                    else:
                        current_section = None
                        current_gate_key = None
                continue

            if current_section == "gates":
                if indent == 2:
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
    root_path = os.path.join(SFLO_ROOT, "pipeline.yaml")
    if os.path.isfile(root_path):
        return root_path
    return None


def load_pipeline_config(path=None):
    """Load and merge pipeline config. Falls back to built-in defaults.

    Returns a dict with keys:
      - gates: dict mapping numeric key -> {artifact, role, gate_doc}
      - grade_threshold: numeric threshold value
    """
    resolved = path or resolve_pipeline_path()

    raw = {}
    if resolved:
        parsed, err = parse_pipeline_yaml(resolved)
        if parsed is not None:
            raw = parsed

    threshold_str = raw.get("threshold", _DEFAULTS["threshold"])
    grade_threshold = _GRADE_MAP.get(threshold_str, _DEFAULTS["threshold"])
    if not isinstance(grade_threshold, (int, float)):
        grade_threshold = _GRADE_MAP[_DEFAULTS["threshold"]]

    gates_raw = raw.get("gates", {})
    if gates_raw:
        gates = {k: gates_raw[k] for k in sorted(gates_raw.keys())}
    else:
        gates = dict(_DEFAULTS["gates"])

    return {
        "gates": gates,
        "grade_threshold": grade_threshold,
    }
