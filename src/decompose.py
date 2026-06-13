"""SFLO mechanical work-breakdown generator.

Parses SCOPE.md deliverables section → builds dependency DAG →
generates WORK-BREAKDOWN.md with topological ordering, epic grouping,
critical path, and AC coverage.

Zero LLM cost. Provably correct ordering via graph algorithms.
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field


# --- Data Model ---


@dataclass
class Deliverable:
    id: int
    title: str
    ac_refs: list
    depends_on: list
    complexity: str  # S, M, L, XL

    @property
    def points(self):
        return {"S": 3, "M": 5, "L": 8, "XL": 13}.get(self.complexity, 5)


@dataclass
class EpicGroup:
    id: int
    name: str
    wp_ids: list
    requires_epics: list = field(default_factory=list)
    description: str = ""


# --- Parsing ---

# Pattern: N. **title** — AC refs. Depends: DN[, DN]. [Complexity]
# Also handles: No deps.
_DELIVERABLE_LINE = re.compile(
    r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*[—–-]\s*(.+)$", re.MULTILINE
)
_AC_REF = re.compile(r"AC(\d+)")
_DEP_SINGLE = re.compile(r"D(\d+)")
_DEP_RANGE = re.compile(r"D(\d+)\s*[-–]\s*D(\d+)")
_COMPLEXITY = re.compile(r"\[([SMLX]{1,2})\]\s*$")


def parse_deliverables(content: str) -> list:
    """Extract deliverables from SCOPE.md content."""
    deliverables = []
    for m in _DELIVERABLE_LINE.finditer(content):
        d_id = int(m.group(1))
        title = m.group(2).strip()
        rest = m.group(3)

        # Extract AC refs
        ac_refs = [f"AC{x}" for x in _AC_REF.findall(rest)]

        # Extract complexity from end
        complexity_match = _COMPLEXITY.search(rest)
        complexity = complexity_match.group(1) if complexity_match else "M"

        # Extract dependencies
        deps = set()
        for range_m in _DEP_RANGE.finditer(rest):
            start, end = int(range_m.group(1)), int(range_m.group(2))
            deps.update(range(start, end + 1))
        # Individual deps (not in ranges)
        cleaned = _DEP_RANGE.sub("", rest)
        # Only look at deps after "Depends" keyword
        dep_section = ""
        for keyword in ("Depends:", "Depends on:", "Deps:"):
            idx = cleaned.find(keyword)
            if idx >= 0:
                dep_section = cleaned[idx:]
                break
        if dep_section:
            for ref in _DEP_SINGLE.findall(dep_section):
                deps.add(int(ref))

        deliverables.append(Deliverable(
            id=d_id,
            title=title,
            ac_refs=ac_refs,
            depends_on=sorted(deps),
            complexity=complexity,
        ))
    return deliverables


# --- DAG Operations ---


def compute_depth(deliverables: list) -> dict:
    """Compute depth (longest path from root) for each deliverable."""
    by_id = {d.id: d for d in deliverables}
    cache = {}

    def _depth(d_id: int) -> int:
        if d_id in cache:
            return cache[d_id]
        d = by_id.get(d_id)
        if not d or not d.depends_on:
            cache[d_id] = 0
            return 0
        valid_deps = [dep for dep in d.depends_on if dep in by_id]
        if not valid_deps:
            cache[d_id] = 0
            return 0
        result = 1 + max(_depth(dep) for dep in valid_deps)
        cache[d_id] = result
        return result

    for d in deliverables:
        _depth(d.id)
    return cache


def topological_layers(deliverables: list) -> list:
    """Return layers: each layer contains IDs executable in parallel."""
    depths = compute_depth(deliverables)
    layers = defaultdict(list)
    for d_id, depth in sorted(depths.items()):
        layers[depth].append(d_id)
    return [layers[k] for k in sorted(layers.keys())]


def find_critical_path(deliverables: list) -> list:
    """Find longest path through DAG (by node count)."""
    by_id = {d.id: d for d in deliverables}
    depths = compute_depth(deliverables)
    if not depths:
        return []

    max_depth = max(depths.values())
    # Pick deepest node (break ties by ID for determinism)
    deepest = min(
        (d_id for d_id, dep in depths.items() if dep == max_depth),
        key=lambda x: x,
    )

    # Trace back from deepest
    path = [deepest]
    current = deepest
    while by_id[current].depends_on:
        valid_deps = [d for d in by_id[current].depends_on if d in by_id]
        if not valid_deps:
            break
        pred = max(valid_deps, key=lambda x: depths.get(x, 0))
        path.append(pred)
        current = pred
    return list(reversed(path))


def validate_dag(deliverables: list) -> list:
    """Return list of errors (empty = valid)."""
    by_id = {d.id: d for d in deliverables}
    errors = []

    for d in deliverables:
        for dep in d.depends_on:
            if dep not in by_id:
                errors.append(f"D{d.id} depends on D{dep} which doesn't exist")

    # Cycle detection via DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {d.id: WHITE for d in deliverables}

    def dfs(node):
        color[node] = GRAY
        d = by_id.get(node)
        if d:
            for dep in d.depends_on:
                if dep in color:
                    if color[dep] == GRAY:
                        errors.append(f"Cycle detected involving D{node} → D{dep}")
                    elif color[dep] == WHITE:
                        dfs(dep)
        color[node] = BLACK

    for d in deliverables:
        if color[d.id] == WHITE:
            dfs(d.id)

    return errors


# --- Epic Grouping ---


def cluster_into_epics(deliverables: list, max_per_epic: int = 5) -> list:
    """Group deliverables into epics by dependency depth layers.

    Adjacent layers are merged if their combined size <= max_per_epic.
    """
    layers = topological_layers(deliverables)
    by_id = {d.id: d for d in deliverables}

    epics = []
    current_wp_ids = []
    current_requires = []

    for layer in layers:
        if current_wp_ids and len(current_wp_ids) + len(layer) > max_per_epic:
            # Flush current epic
            epics.append(EpicGroup(
                id=len(epics) + 1,
                name="",  # named below
                wp_ids=current_wp_ids,
                requires_epics=[len(epics)] if epics else [],
            ))
            current_wp_ids = list(layer)
        else:
            current_wp_ids.extend(layer)

    # Flush remaining
    if current_wp_ids:
        epics.append(EpicGroup(
            id=len(epics) + 1,
            name="",
            wp_ids=current_wp_ids,
            requires_epics=[len(epics)] if epics else [],
        ))

    # Name epics based on position
    _NAMES = [
        "Foundation",
        "Core Services",
        "Business Logic",
        "User Interface",
        "Integration",
        "Extensions",
        "Verification",
    ]
    for i, epic in enumerate(epics):
        epic.name = _NAMES[i] if i < len(_NAMES) else f"Phase {i + 1}"

    return epics


# --- Formatting ---


def format_work_breakdown(
    deliverables: list,
    epic_groups: list,
    critical_path: list,
    parallelization: list,
) -> str:
    """Generate WORK-BREAKDOWN.md content."""
    by_id = {d.id: d for d in deliverables}
    complexity_counts = defaultdict(int)
    for d in deliverables:
        complexity_counts[d.complexity] += 1
    total_points = sum(d.points for d in deliverables)

    lines = [
        "# WORK-BREAKDOWN.md",
        "<!-- Generated by SFLO runner (mechanical decomposition) -->",
        "",
        "## Meta",
        f"- Source: SCOPE.md",
        f"- Total WPs: {len(deliverables)}",
        f"- Epic count: {len(epic_groups)}",
        f"- Critical path: {' → '.join(f'WP-{x}' for x in critical_path)} "
        f"({len(critical_path)} steps)",
        f"- Effort: {' + '.join(f'{v}{k}' for k, v in sorted(complexity_counts.items()))}",
        "",
    ]

    # Epics with WP tables
    for epic in epic_groups:
        requires = (
            f"**Requires: Epic {epic.requires_epics[0]} complete**"
            if epic.requires_epics
            else "**No prerequisites**"
        )
        lines.append(f"## Epic {epic.id}: {epic.name}")
        lines.append(requires)
        lines.append("")
        lines.append("| WP | Title | Complexity | ACs | Depends on |")
        lines.append("|-----|-------|-----------|-----|------------|")
        for wp_id in epic.wp_ids:
            d = by_id[wp_id]
            deps_str = ", ".join(f"WP-{x}" for x in d.depends_on) or "—"
            ac_str = ", ".join(d.ac_refs) or "—"
            lines.append(
                f"| WP-{d.id} | {d.title} | {d.complexity} | {ac_str} | {deps_str} |"
            )
        lines.append("")

    # Critical path
    lines.append("## Critical Path")
    lines.append("```")
    lines.append(" → ".join(f"WP-{x}" for x in critical_path))
    lines.append("```")
    lines.append("")

    # Parallelization
    if parallelization:
        lines.append("## Parallelization Opportunities")
        for group in parallelization:
            if len(group) > 1:
                lines.append(
                    f"- {' ∥ '.join(f'WP-{x}' for x in group)}"
                )
        lines.append("")

    # AC coverage
    lines.append("## AC Coverage")
    lines.append("| AC | WP |")
    lines.append("|----|-----|")
    for d in deliverables:
        for ac in d.ac_refs:
            lines.append(f"| {ac} | WP-{d.id} |")
    lines.append("")

    return "\n".join(lines)


# --- Entry Point ---


def generate_work_breakdown(scope_path: str, output_path: str) -> str:
    """Parse SCOPE.md → write WORK-BREAKDOWN.md. Returns content."""
    with open(scope_path, "r", encoding="utf-8") as f:
        content = f.read()

    deliverables = parse_deliverables(content)
    if not deliverables:
        raise ValueError(
            f"No deliverables found in {scope_path}. "
            f"PM must include ## Deliverables section."
        )

    # Validate DAG
    errors = validate_dag(deliverables)
    if errors:
        raise ValueError(
            f"Invalid dependency graph in {scope_path}: {'; '.join(errors)}"
        )

    # Compute structure
    layers = topological_layers(deliverables)
    critical_path = find_critical_path(deliverables)
    epic_groups = cluster_into_epics(deliverables)

    # Format
    wb_content = format_work_breakdown(
        deliverables=deliverables,
        epic_groups=epic_groups,
        critical_path=critical_path,
        parallelization=layers,
    )

    # Write
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(wb_content)

    return wb_content


def generate_current_epic(
    wb_path: str,
    epic_index: int,
    prior_statuses: list = None,
    output_path: str = None,
) -> str:
    """Generate CURRENT-EPIC.md for a specific epic.

    Reads WORK-BREAKDOWN.md, extracts the specified epic's WPs,
    and formats a focused dev context.
    """
    with open(wb_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse epic section (regex-based, good enough for structured output)
    epic_pattern = re.compile(
        rf"## Epic {epic_index}:(.+?)(?=\n## Epic \d|## Critical Path|\Z)",
        re.DOTALL,
    )
    match = epic_pattern.search(content)
    if not match:
        raise ValueError(f"Epic {epic_index} not found in {wb_path}")

    epic_name = match.group(0).split("\n")[0].replace(f"## Epic {epic_index}:", "").strip()
    epic_content = match.group(0)

    # Count total epics
    total_epics = len(re.findall(r"## Epic \d+:", content))

    lines = [
        f"# CURRENT-EPIC: {epic_name} ({epic_index}/{total_epics})",
        "",
        epic_content,
        "",
    ]

    if prior_statuses:
        lines.append("## Prior Epic Outputs (context)")
        for status in prior_statuses:
            lines.append(f"- {status}")
        lines.append("")

    lines.append("## Completion Criteria")
    lines.append("All WPs in this epic pass their acceptance tests.")
    lines.append("Produce BUILD-STATUS.md with per-WP status.")
    lines.append("")

    result = "\n".join(lines)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result
