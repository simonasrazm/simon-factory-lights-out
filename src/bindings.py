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


def resolve_bindings_path(explicit=None):
    """Resolve bindings.yaml: explicit -> cwd -> sflo/ subdir."""
    if explicit and os.path.isfile(explicit):
        return explicit
    cwd_path = os.path.join(os.getcwd(), "bindings.yaml")
    if os.path.isfile(cwd_path):
        return cwd_path
    sflo_path = os.path.join(os.getcwd(), "sflo", "bindings.yaml")
    if os.path.isfile(sflo_path):
        return sflo_path
    return None
