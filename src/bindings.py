"""SFLO bindings.yaml parser — strict 2-level YAML subset."""

import os


def parse_bindings(path):
    """Parse bindings.yaml — flat 2-level YAML with role->{model, thinking, agent}.

    Supported subset:
    - Top-level key must be 'roles:'
    - Role names at 2-space indent ending with ':'
    - Key-value pairs at 4+ space indent, split on FIRST colon only
    - Tabs in indentation are rejected
    - Comments (#) and blank lines are skipped
    """
    if not os.path.isfile(path):
        return None, f"File not found: {path}"

    roles = {}
    current_role = None
    in_roles = False
    errors = []

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            raw = line.rstrip("\n\r")
            content = raw.strip()
            if not content or content.startswith("#"):
                continue

            if "\t" in raw[:len(raw) - len(raw.lstrip())]:
                errors.append(f"Line {line_num}: tabs in indentation not supported")
                continue

            indent = len(raw) - len(raw.lstrip())

            if indent == 0:
                if content.rstrip(":") == "roles":
                    in_roles = True
                else:
                    in_roles = False
                current_role = None
                continue

            if not in_roles:
                continue

            if indent == 2 and content.endswith(":"):
                current_role = content[:-1].strip()
                roles[current_role] = {}
                continue

            if indent >= 4 and current_role and ":" in content:
                key, _, val = content.partition(":")
                roles[current_role][key.strip()] = val.strip()

    if errors:
        return None, f"Parse errors in {path}: {'; '.join(errors)}"

    if not roles:
        return None, f"No roles found in {path}"

    return roles, None


def _read_top_level_flag(bindings_path, key):
    """Read a comma-separated top-level bindings.yaml flag.

    Format: `<key>: a, b, c` on its own line at the top of the file
    (outside any `roles:` block). Returns a set of stripped values.
    Empty set if the flag is missing or bindings.yaml doesn't exist.
    """
    if not bindings_path or not os.path.isfile(bindings_path):
        return set()
    prefix = f"{key}:"
    with open(bindings_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(prefix):
                vals = stripped.split(":", 1)[1].strip()
                return {v.strip() for v in vals.split(",") if v.strip()}
    return set()


def load_exclude_agents(bindings_path=None):
    """Return the set of agent entry names to exclude from scout listings.

    Configured via `exclude_agents: pm-website, foo, bar` at top-level of
    bindings.yaml. Matches by directory entry name (e.g. "pm-website")
    across ALL agent_dirs the runner scans. Too coarse to selectively
    block a single directory's copy of a shared name — use
    `exclude_agent_dirs` for directory-level exclusion.
    """
    if bindings_path is None:
        bindings_path = resolve_bindings_path()
    return _read_top_level_flag(bindings_path, "exclude_agents")


def load_exclude_agent_dirs(bindings_path=None):
    """Return the set of agent directory paths to exclude from scout listings.

    Configured via `exclude_agent_dirs: sflo/agents, vendor/agents` at
    top-level of bindings.yaml. Matches by dir path containing the
    configured substring — use this to hide an entire dir of agents
    from scout (e.g. block upstream agents when host project
    provides local overrides).
    """
    if bindings_path is None:
        bindings_path = resolve_bindings_path()
    return _read_top_level_flag(bindings_path, "exclude_agent_dirs")


def resolve_bindings_path(explicit=None):
    """Resolve bindings.yaml, preferring local overrides over the submodule default.

    Resolution order (first hit wins):
      1. explicit path (must exist, else None)
      2. cwd/bindings.yaml
      3. cwd/sflo/bindings.yaml
      4. SFLO_PARENT/bindings.yaml  (host project config, one level above submodule)
      5. SFLO_ROOT/bindings.yaml    (submodule default)

    #4 supports the vendored submodule pattern where the host project
    provides its own bindings.yaml with exclusions, local agent dirs,
    role overrides, etc.

    If `explicit` is given, it MUST exist. Falling back silently to a
    different file when the caller asked for a specific one would hide the
    caller's mistake — return None so the caller errors out instead.
    """
    from .constants import SFLO_ROOT

    if explicit:
        return explicit if os.path.isfile(explicit) else None

    cwd_path = os.path.join(os.getcwd(), "bindings.yaml")
    if os.path.isfile(cwd_path):
        return cwd_path
    sflo_path = os.path.join(os.getcwd(), "sflo", "bindings.yaml")
    if os.path.isfile(sflo_path):
        return sflo_path
    # Host project config (one level above the submodule) takes precedence
    # over the submodule's own bindings.yaml.
    parent_path = os.path.join(os.path.dirname(SFLO_ROOT), "bindings.yaml")
    if os.path.isfile(parent_path):
        return parent_path
    root_path = os.path.join(SFLO_ROOT, "bindings.yaml")
    if os.path.isfile(root_path):
        return root_path
    return None
