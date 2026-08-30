"""SFLO Gate 1.5 validation — WORK-BREAKDOWN.md structural checks.

Validates that a WORK-BREAKDOWN.md artifact meets structural requirements
before dev starts building. All checks are mechanical (no LLM) and run in <1s.

Checks:
1. AC coverage — every AC from SCOPE.md mapped to exactly one WP
2. DAG validity — no circular dependencies
3. Epic ordering — no WP depends on a WP in a later epic
4. Contract specificity — integration contracts have concrete shapes (not vague)
5. WP completeness — each WP has acceptance criteria

Usage:
    from .validate_wb import validate_work_breakdown
    errors = validate_work_breakdown(wb_content, scope_content)
"""

import re
from dataclasses import dataclass


@dataclass
class ValidationCheck:
    name: str
    passed: bool
    detail: str = ""


def validate_work_breakdown(wb_content: str, scope_content: str = "") -> list:
    """Run all structural validation checks on WORK-BREAKDOWN.md.

    Args:
        wb_content: Full WORK-BREAKDOWN.md content
        scope_content: Full SCOPE.md content (for AC cross-reference)

    Returns:
        List of ValidationCheck results. All passed = valid WB.
    """
    checks = []
    checks.append(check_ac_coverage(wb_content, scope_content))
    checks.append(check_dag_validity(wb_content))
    checks.append(check_epic_ordering(wb_content))
    checks.append(check_contract_specificity(wb_content))
    checks.append(check_wp_completeness(wb_content))
    checks.append(check_no_orphan_wps(wb_content))
    return checks


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_ac_coverage(wb_content: str, scope_content: str) -> ValidationCheck:
    """Every AC from SCOPE.md must be mapped to exactly one WP."""
    name = "ac_coverage"

    # Extract ACs from SCOPE.md
    scope_acs = set(re.findall(r"AC(\d+)", scope_content))
    if not scope_acs:
        return ValidationCheck(name=name, passed=True, detail="No ACs in SCOPE.md to check")

    # Extract AC→WP mappings from WB
    # Patterns: "AC1 → WP-1" or "| AC1 | WP-1 |" or "AC1, AC2" in WP section
    wb_acs = set(re.findall(r"AC(\d+)", wb_content))

    missing = scope_acs - wb_acs
    if missing:
        missing_str = ", ".join(f"AC{x}" for x in sorted(missing, key=int))
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"ACs not mapped to any WP: {missing_str}",
        )

    return ValidationCheck(name=name, passed=True)


def check_dag_validity(wb_content: str) -> ValidationCheck:
    """No circular dependencies in WP graph."""
    name = "dag_valid"

    # Parse WP dependencies from WB
    # Pattern: "| WP-N | ... | WP-X, WP-Y |" or "**Dependencies**: WP-1, WP-3"
    wp_deps = _parse_wp_dependencies(wb_content)

    if not wp_deps:
        return ValidationCheck(name=name, passed=True, detail="No WPs found")

    # Cycle detection via DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    colors = {wp: WHITE for wp in wp_deps}
    cycles = []

    def dfs(node, path):
        colors[node] = GRAY
        for dep in wp_deps.get(node, []):
            if dep not in colors:
                continue
            if colors[dep] == GRAY:
                cycles.append(f"WP-{node} → WP-{dep}")
            elif colors[dep] == WHITE:
                dfs(dep, path + [node])
        colors[node] = BLACK

    for wp in wp_deps:
        if colors[wp] == WHITE:
            dfs(wp, [])

    if cycles:
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"Circular dependencies: {'; '.join(cycles)}",
        )

    return ValidationCheck(name=name, passed=True)


def check_epic_ordering(wb_content: str) -> ValidationCheck:
    """No WP in Epic N should depend on a WP in Epic M where M > N."""
    name = "epic_ordering"

    # Parse epic assignments
    epic_assignments = _parse_epic_wp_assignments(wb_content)
    if not epic_assignments:
        return ValidationCheck(name=name, passed=True, detail="No epic groups found")

    # Build WP → epic mapping
    wp_to_epic = {}
    for epic_id, wp_ids in epic_assignments.items():
        for wp_id in wp_ids:
            wp_to_epic[wp_id] = epic_id

    # Parse dependencies
    wp_deps = _parse_wp_dependencies(wb_content)

    violations = []
    for wp_id, deps in wp_deps.items():
        wp_epic = wp_to_epic.get(wp_id)
        if wp_epic is None:
            continue
        for dep_id in deps:
            dep_epic = wp_to_epic.get(dep_id)
            if dep_epic is not None and dep_epic > wp_epic:
                violations.append(
                    f"WP-{wp_id} (Epic {wp_epic}) depends on WP-{dep_id} (Epic {dep_epic})"
                )

    if violations:
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"Forward epic dependencies: {'; '.join(violations)}",
        )

    return ValidationCheck(name=name, passed=True)


def check_contract_specificity(wb_content: str) -> ValidationCheck:
    """Integration contracts must have concrete shapes, not vague descriptions."""
    name = "contract_specificity"

    # Find PROVIDES sections
    provides_sections = re.findall(
        r"PROVIDES.*?\n(.*?)(?=REQUIRES|###|\Z)",
        wb_content,
        re.DOTALL | re.IGNORECASE,
    )

    if not provides_sections:
        # No contracts = OK for single-epic or simple WBs
        return ValidationCheck(name=name, passed=True, detail="No contracts to check")

    vague_patterns = [
        r"provides?\s+(?:an?\s+)?(?:api|interface|service)\b(?!\s+\w+\s+(?:endpoint|method|schema|type|model))",
        r"exposes?\s+(?:functionality|capability|features?)\b",
        r"handles?\s+(?:things|stuff|everything)\b",
    ]

    vague_items = []
    for section in provides_sections:
        items = re.findall(r"^-\s*(.+)$", section, re.MULTILINE)
        for item in items:
            for pattern in vague_patterns:
                if re.search(pattern, item, re.IGNORECASE):
                    vague_items.append(item.strip()[:60])
                    break

    if vague_items:
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"Vague contracts ({len(vague_items)}): {'; '.join(vague_items[:3])}",
        )

    return ValidationCheck(name=name, passed=True)


def check_wp_completeness(wb_content: str) -> ValidationCheck:
    """Each WP must have acceptance criteria that are independently testable."""
    name = "wp_completeness"

    # Find WP sections
    wp_sections = re.findall(
        r"###?\s*WP-(\d+)\s*:.*?(?=###?\s*WP-|\Z)",
        wb_content,
        re.DOTALL,
    )

    if not wp_sections:
        return ValidationCheck(name=name, passed=True, detail="No WP sections found")

    # For each WP, check it has acceptance/test criteria
    acceptance_patterns = [
        r"\*\*Acceptance\*\*",
        r"acceptance\s*:",
        r"\*\*Test\*\*",
        r"test\s*assertion",
        r"verified\s+by",
    ]

    # Parse WP blocks with content
    wp_blocks = re.finditer(
        r"(###?\s*WP-(\d+)\s*:[^\n]*\n.*?)(?=###?\s*WP-|\Z)",
        wb_content,
        re.DOTALL,
    )

    missing_acceptance = []
    for block_match in wp_blocks:
        wp_id = int(block_match.group(2))
        block = block_match.group(1)
        has_acceptance = any(
            re.search(p, block, re.IGNORECASE) for p in acceptance_patterns
        )
        if not has_acceptance:
            missing_acceptance.append(f"WP-{wp_id}")

    if missing_acceptance:
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"WPs missing acceptance criteria: {', '.join(missing_acceptance[:5])}",
        )

    return ValidationCheck(name=name, passed=True)


def check_no_orphan_wps(wb_content: str) -> ValidationCheck:
    """All WPs mentioned in epic groups must exist as WP sections."""
    name = "no_orphan_wps"

    epic_assignments = _parse_epic_wp_assignments(wb_content)
    all_epic_wps = set()
    for wp_ids in epic_assignments.values():
        all_epic_wps.update(wp_ids)

    # Find defined WP sections
    defined_wps = set(int(x) for x in re.findall(r"###?\s*WP-(\d+)\s*:", wb_content))

    if not all_epic_wps:
        return ValidationCheck(name=name, passed=True, detail="No epic assignments")

    orphans = all_epic_wps - defined_wps
    if orphans:
        orphan_str = ", ".join(f"WP-{x}" for x in sorted(orphans))
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"WPs in epic groups but not defined: {orphan_str}",
        )

    unassigned = defined_wps - all_epic_wps
    if unassigned:
        unassigned_str = ", ".join(f"WP-{x}" for x in sorted(unassigned))
        return ValidationCheck(
            name=name,
            passed=False,
            detail=f"WPs defined but not in any epic: {unassigned_str}",
        )

    return ValidationCheck(name=name, passed=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_wp_dependencies(wb_content: str) -> dict:
    """Parse WP-to-WP dependencies from WB content.

    Returns dict: wp_id -> list of dependency wp_ids
    """
    deps = {}

    # Pattern 1: Table row "| WP-N | title | ... | WP-X, WP-Y |"
    table_rows = re.findall(
        r"\|\s*WP-(\d+)\s*\|.*?\|\s*([^|]*?)\s*\|?\s*$",
        wb_content,
        re.MULTILINE,
    )
    for wp_id_str, last_col in table_rows:
        wp_id = int(wp_id_str)
        dep_ids = [int(x) for x in re.findall(r"WP-(\d+)", last_col)]
        if wp_id not in deps:
            deps[wp_id] = dep_ids
        else:
            deps[wp_id].extend(dep_ids)

    # Pattern 2: "**Dependencies**: WP-1, WP-3" in WP sections
    dep_lines = re.findall(
        r"\*\*Dependencies?\*\*\s*:\s*([^\n]+)",
        wb_content,
    )
    # Need context to know which WP this belongs to
    wp_sections = re.finditer(
        r"###?\s*WP-(\d+)\s*:.*?(?=###?\s*WP-|\Z)",
        wb_content,
        re.DOTALL,
    )
    for section_match in wp_sections:
        wp_id = int(section_match.group(1))
        section = section_match.group(0)
        dep_match = re.search(r"\*\*Dependencies?\*\*\s*:\s*([^\n]+)", section)
        if dep_match:
            dep_text = dep_match.group(1)
            if "none" not in dep_text.lower() and "—" not in dep_text:
                dep_ids = [int(x) for x in re.findall(r"WP-(\d+)", dep_text)]
                deps[wp_id] = dep_ids
        elif wp_id not in deps:
            deps[wp_id] = []

    return deps


def _parse_epic_wp_assignments(wb_content: str) -> dict:
    """Parse which WPs belong to which epic.

    Returns dict: epic_id -> list of wp_ids
    """
    assignments = {}

    # Pattern: "### Epic N: ..." section containing "WP-X" references
    # or "**WPs**: WP-1, WP-2, WP-3"
    epic_sections = re.finditer(
        r"(?:###?\s*Epic\s+(\d+)\s*:.*?)((?=###?\s*Epic\s+\d+\s*:)|(?=\n##\s[^#])|\Z)",
        wb_content,
        re.DOTALL,
    )

    for match in epic_sections:
        epic_id = int(match.group(1))
        section = match.group(0)

        # Look for explicit WPs list
        wp_list_match = re.search(r"\*\*WPs?\*\*\s*:\s*([^\n]+)", section)
        if wp_list_match:
            wp_ids = [int(x) for x in re.findall(r"(?:WP-)?(\d+)", wp_list_match.group(1))]
            assignments[epic_id] = wp_ids
        else:
            # Fall back to table rows
            table_wps = [int(x) for x in re.findall(r"\|\s*WP-(\d+)\s*\|", section)]
            if table_wps:
                assignments[epic_id] = table_wps

    return assignments
