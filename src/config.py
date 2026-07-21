"""SFLO pipeline.yaml config loader — hand-rolled YAML parser, no external deps."""

import os


# Grade scale — domain constant mapping letter grades to numeric values.
# Used by validate.py for threshold comparison and by load_pipeline_config
# to resolve the threshold: field. Single definition, re-exported via constants.py.
GRADE_MAP = {"A": 6, "A-": 5.5, "B+": 5, "B": 4, "B-": 3.5, "C": 3, "D": 2, "F": 1}


# Gate fields that are simple scalars (key: value)
_GATE_SCALAR_FIELDS = {
    "artifact",
    "role",
    "gate_doc",
    "model",
    "agent",
    "runner",
    "validator",
    "on_reject_restart_at",
    "threshold",
    "thinking",
    "effort",
    "tools",
}

# Gate fields that are lists (key: followed by indented "- item" lines)
_GATE_LIST_FIELDS = {"skills", "agents", "mcp"}

# Gate fields that are booleans (parsed as true/false, not strings)
_GATE_BOOL_FIELDS = {"allow_task"}


def _parse_bool(val):
    """Parse a YAML-style boolean string → Python bool. Returns None if ambiguous."""
    if val.lower() in ("true", "yes", "on"):
        return True
    if val.lower() in ("false", "no", "off"):
        return False
    return None


def _coerce_field(key, val):
    """Coerce a gate field value based on its type declaration.

    Checks _GATE_BOOL_FIELDS and converts string → bool where applicable.
    Returns the (possibly coerced) value.
    """
    if key in _GATE_BOOL_FIELDS and isinstance(val, str):
        parsed = _parse_bool(val)
        if parsed is not None:
            return parsed
    return val


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
    """Parse pipeline.yaml — supports threshold, gates, scout, sflo sections.

    Supported subset:
    - Top-level keys: threshold, gates, scout, sflo, security,
      exclude_agents, exclude_agent_dirs
    - Gate entries: 2-space indent under gates:, then 4-space for fields
    - Gate keys as numbers (integers or floats like 1.5)
    - List-based parallel gates: 4-space indent starting with "- " under a gate key
    - Per-gate sub-lists: skills/agents as "- item" at deeper indent
    - Comments (#) and blank lines are skipped
    - Tabs in indentation are rejected

    Returns (config_dict, error_string). config_dict is None on error.
    """
    if not os.path.isfile(path):
        return None, f"File not found: {path}"

    result = {}
    current_section = None
    current_gate_key = None
    # For list-based parallel gates: tracks whether current gate is a list
    in_list_item = False
    # Track which gate-level sub-list we're currently reading (skills/agents)
    current_gate_sublist = None
    current_simple_sublist = None
    # For simple top-level sections (scout, sflo, security)
    # (removed unused current_simple_section)
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
                current_gate_sublist = None
                current_simple_sublist = None
                if ":" in content:
                    key, _, val = content.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if key == "threshold":
                        result["threshold"] = (
                            _strip_inline_comment(val) if val else "B+"
                        )
                        current_section = "threshold"
                        current_gate_key = None
                    elif key == "gates":
                        result["gates"] = {}
                        current_section = "gates"
                        current_gate_key = None
                    elif key in ("scout", "sflo", "security"):
                        result[key] = {}
                        current_section = key
                        current_gate_key = None
                    elif key in ("exclude_agents", "exclude_agent_dirs"):
                        # Inline comma-separated values
                        val = _strip_inline_comment(val)
                        result[key] = (
                            [v.strip() for v in val.split(",") if v.strip()]
                            if val
                            else []
                        )
                        current_section = None
                        current_gate_key = None
                    else:
                        current_section = None
                        current_gate_key = None
                continue

            # Simple top-level sections (scout, sflo, security): key-value at indent 2
            if current_section in ("scout", "sflo", "security"):
                if indent == 2 and ":" in content:
                    key, _, val = content.partition(":")
                    key = key.strip()
                    val = _strip_inline_comment(val)
                    if key == "skills" and not val:
                        result[current_section][key] = []
                        current_simple_sublist = key
                    else:
                        result[current_section][key] = val
                        current_simple_sublist = None
                elif indent == 4 and current_simple_sublist and content.startswith("- "):
                    result[current_section][current_simple_sublist].append(
                        _strip_inline_comment(content[2:])
                    )
                continue

            if current_section == "gates":
                # Gate key at indent 2
                if indent == 2:
                    current_gate_sublist = None
                    if content.endswith(":"):
                        gate_str = content[:-1].strip()
                        gate_key = _parse_gate_key(gate_str)
                        if gate_key is not None:
                            current_gate_key = gate_key
                            result["gates"][gate_key] = {}
                            in_list_item = False
                        else:
                            errors.append(
                                f"Line {line_num}: invalid gate key: {gate_str!r}"
                            )
                            current_gate_key = None
                    continue

                # Non-parallel gate fields at indent 4
                if indent == 4 and current_gate_key is not None:
                    # List item start for parallel gate: "- key: value"
                    if content.startswith("- "):
                        current_gate_sublist = None
                        item_content = content[2:].strip()
                        # Convert gate value to list if not already
                        if not isinstance(result["gates"][current_gate_key], list):
                            result["gates"][current_gate_key] = []
                        # Parse first field of new list item
                        new_item = {}
                        if ":" in item_content:
                            key, _, val = item_content.partition(":")
                            key = key.strip()
                            val = _strip_inline_comment(val)
                            val = _coerce_field(key, val)
                            new_item[key] = val
                        result["gates"][current_gate_key].append(new_item)
                        in_list_item = True
                        continue

                    if ":" in content:
                        key, _, val = content.partition(":")
                        key = key.strip()
                        val = _strip_inline_comment(val)

                        # Check if this starts a sub-list (skills:, agents:)
                        if key in _GATE_LIST_FIELDS and not val:
                            result["gates"][current_gate_key][key] = []
                            current_gate_sublist = key
                            continue

                        if key == "on_reject_restart_at":
                            parsed_val = _parse_gate_key(val)
                            val = parsed_val if parsed_val is not None else val
                        val = _coerce_field(key, val)
                        current_gate_sublist = None
                        result["gates"][current_gate_key][key] = val
                    continue

                # Indent 6: sub-list items OR parallel gate continuation fields
                if indent == 6 and current_gate_key is not None:
                    gate_val = result["gates"][current_gate_key]
                    # Resolve the target dict for field/sublist writes
                    target = None
                    if in_list_item and isinstance(gate_val, list) and gate_val:
                        target = gate_val[-1]
                    elif not in_list_item and isinstance(gate_val, dict):
                        target = gate_val

                    if target is not None:
                        # Sub-list item (skills/agents)
                        if current_gate_sublist and content.startswith("- "):
                            item_val = _strip_inline_comment(content[2:])
                            if current_gate_sublist in target:
                                target[current_gate_sublist].append(item_val)
                        # Continuation key: value field
                        elif ":" in content:
                            key, _, val = content.partition(":")
                            key = key.strip()
                            val = _strip_inline_comment(val)
                            if key in _GATE_LIST_FIELDS and not val:
                                target[key] = []
                                current_gate_sublist = key
                            else:
                                current_gate_sublist = None
                                val = _coerce_field(key, val)
                                target[key] = val
                    continue

                # Indent 8: sub-list items inside parallel gate entry
                if indent == 8 and current_gate_key is not None and in_list_item:
                    if current_gate_sublist and content.startswith("- "):
                        item_val = _strip_inline_comment(content[2:])
                        gate_val = result["gates"][current_gate_key]
                        if isinstance(gate_val, list) and gate_val:
                            last_entry = gate_val[-1]
                            if current_gate_sublist in last_entry:
                                last_entry[current_gate_sublist].append(item_val)
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
    """Load pipeline config from pipeline.yaml.

    Returns a dict with keys:
      - gates: dict mapping numeric key -> {artifact, role, gate_doc, ...}
      - grade_threshold: numeric threshold value (global default)
      - scout: dict with scout config (model, thinking, effort, tools, agent)
      - sflo: dict with sflo orchestrator config
      - security: dict with security toggles
      - exclude_agents: list of agent names to exclude
      - exclude_agent_dirs: list of agent dir paths to exclude
      - _missing: True if no pipeline.yaml found (preflight catches this)
    """
    if path:
        resolved = path if os.path.isfile(path) else None
    else:
        resolved = resolve_pipeline_path()

    if not resolved:
        # No pipeline.yaml anywhere. Return sentinel for preflight to catch.
        return {
            "_missing": True,
            "gates": {},
            "grade_threshold": 5,
            "scout": {},
            "sflo": {},
            "security": {},
            "exclude_agents": [],
            "exclude_agent_dirs": [],
        }

    raw = {}
    parsed, err = parse_pipeline_yaml(resolved)
    if parsed is not None:
        raw = parsed
    elif err:
        # pipeline.yaml exists but has parse errors — surface as _error sentinel
        return {
            "_error": err,
            "gates": {},
            "grade_threshold": 5,
            "scout": {},
            "sflo": {},
            "security": {},
            "exclude_agents": [],
            "exclude_agent_dirs": [],
        }

    threshold_str = raw.get("threshold", "B+")
    grade_threshold = GRADE_MAP.get(threshold_str, 5)
    if not isinstance(grade_threshold, (int, float)):
        grade_threshold = 5

    gates_raw = raw.get("gates", {})
    gates = {k: gates_raw[k] for k in sorted(gates_raw.keys())} if gates_raw else {}

    return {
        "gates": gates,
        "grade_threshold": grade_threshold,
        "scout": raw.get("scout", {}),
        "sflo": raw.get("sflo", {}),
        "security": raw.get("security", {}),
        "exclude_agents": raw.get("exclude_agents", []),
        "exclude_agent_dirs": raw.get("exclude_agent_dirs", []),
    }


def derive_roles_from_pipeline(config=None):
    """Extract role bindings from pipeline.yaml gates + top-level config.

    Each gate's model/thinking/effort/tools/agent fields become role entries.
    scout: and sflo: top-level sections are also included.
    Returns dict of role name -> {model, thinking, effort, tools, agent}.

    Single source of truth — pipeline.yaml drives all role configuration.
    """
    if config is None:
        config = load_pipeline_config()
    roles = {}

    # Extract from gates
    for gate_info in config.get("gates", {}).values():
        entries = gate_info if isinstance(gate_info, list) else [gate_info]
        for entry in entries:
            role = entry.get("role")
            if not role or role in roles:
                continue
            role_cfg = {}
            for field in ("model", "thinking", "effort", "tools", "agent", "skills"):
                if entry.get(field):
                    role_cfg[field] = entry[field]
            if role_cfg:
                roles[role] = role_cfg

    # Scout and sflo from top-level sections
    for section in ("scout", "sflo"):
        cfg = config.get(section, {})
        if cfg and section not in roles:
            roles[section] = dict(cfg)

    return roles
