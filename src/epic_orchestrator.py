"""SFLO Epic Orchestrator — runner-level epic iteration (Option C).

Implements the epic iteration pattern described in ORCHESTRATOR-ADR.md:
- machine.py stays untouched (pure state machine, unchanged)
- Runner manages epic iteration as execution strategy, not state topology
- Sequential iteration through epics with filtered CURRENT-EPIC.md per epic
- Crash recovery via minimal state extension (epics_completed, current_epic)

Architecture principle:
  "The state machine computes transitions. The runner executes them.
   Epic iteration is execution strategy, not state topology."

Usage (called by runner.py, not directly):
    from .epic_orchestrator import run_epic_iteration, detect_epics

    epics = detect_epics(sflo_dir)
    if len(epics) > 1:
        result = await run_epic_iteration(epics, sflo_dir, state, adapter, ...)
"""

import json
import os
import shutil
import time as _time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EpicSpec:
    """Single epic parsed from WORK-BREAKDOWN.md."""

    id: int  # 1-based epic number
    name: str  # e.g. "Core API endpoints"
    wp_ids: list  # WP IDs in this epic (e.g. [1, 2, 3])
    dependencies: list = field(default_factory=list)  # Epic IDs this depends on
    integration_contract: str = ""  # What this epic exposes to subsequent epics


@dataclass
class EpicResult:
    """Result of a single epic's gate-2 → gate-3 traversal."""

    epic_id: int
    passed: bool
    gate_results: dict = field(default_factory=dict)  # gate_num -> pass/fail
    error: str = ""


@dataclass
class EpicIterationResult:
    """Aggregate result of all epic iterations."""

    all_passed: bool
    epic_results: list = field(default_factory=list)  # list of EpicResult
    failed_epics: list = field(default_factory=list)  # epic IDs that failed
    escalated: bool = False
    escalation_reason: str = ""


# ---------------------------------------------------------------------------
# Epic Detection — parse WORK-BREAKDOWN.md for epic groups
# ---------------------------------------------------------------------------


def detect_epics(sflo_dir: str) -> list:
    """Parse WORK-BREAKDOWN.md to extract epic groups.

    Returns list of EpicSpec. If no epic groups found or only 1 epic,
    returns empty list (caller should use normal linear flow).
    """
    wb_path = os.path.join(sflo_dir, "WORK-BREAKDOWN.md")
    if not os.path.isfile(wb_path):
        return []

    with open(wb_path, "r", encoding="utf-8") as f:
        content = f.read()

    return parse_epic_groups(content)


def parse_epic_groups(content: str) -> list:
    """Extract epic groups from WORK-BREAKDOWN.md content.

    Expected format (from gates/breakdown.md template):
        ## Epic Groups

        ### Epic 1: [Name]
        **WPs**: WP-1, WP-2, WP-3
        **Context**: [what this epic establishes]
        **Integration Contract — PROVIDES:**
        - [interface/schema/service]
        **Integration Contract — REQUIRES:**
        - [upstream requirement]

    Returns list of EpicSpec. Returns empty list if <2 epics found.
    """
    import re

    epics = []

    # Find "## Epic Groups" section
    epic_section_match = re.search(
        r"##\s*Epic\s*Groups?\s*\n(.*?)(?=\n##\s[^#]|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not epic_section_match:
        return []

    section = epic_section_match.group(1)

    # Split into individual epic blocks
    epic_blocks = re.split(r"###\s*Epic\s+(\d+)\s*:\s*", section)

    # epic_blocks[0] is before first match, then alternating: [num, content, num, content, ...]
    i = 1
    while i < len(epic_blocks) - 1:
        epic_num = int(epic_blocks[i])
        block = epic_blocks[i + 1]

        # Extract name (first line of block)
        name_match = re.match(r"([^\n]+)", block.strip())
        name = name_match.group(1).strip() if name_match else f"Epic {epic_num}"

        # Extract WPs
        wp_match = re.search(r"\*\*WPs?\*\*\s*:\s*([^\n]+)", block)
        wp_ids = []
        if wp_match:
            wp_text = wp_match.group(1)
            # Parse "WP-1, WP-2, WP-3" or "1, 2, 3"
            for m in re.finditer(r"(?:WP-)?(\d+)", wp_text):
                wp_ids.append(int(m.group(1)))

        # Extract integration contract (PROVIDES section)
        # Format: **Integration Contract — PROVIDES:**\n- items
        provides_match = re.search(
            r"\*?\*?Integration\s+Contract\s*[—\-]+\s*PROVIDES\s*:?\s*\*?\*?\s*\n(.*?)(?=\*\*Integration|\*\*\w|\n###|\Z)",
            block,
            re.DOTALL | re.IGNORECASE,
        )
        contract = ""
        if provides_match:
            contract = provides_match.group(1).strip()

        # Extract dependencies (REQUIRES section)
        # Format: **Integration Contract — REQUIRES:**\n- Epic N: ...
        requires_match = re.search(
            r"\*?\*?(?:Integration\s+Contract\s*[—\-]+\s*)?REQUIRES\s*:?\s*\*?\*?\s*\n(.*?)(?=\*\*|\n###|\Z)",
            block,
            re.DOTALL | re.IGNORECASE,
        )
        deps = []
        if requires_match:
            req_text = requires_match.group(1)
            # Look for "Epic N" references
            for dep_m in re.finditer(r"Epic\s+(\d+)", req_text):
                dep_id = int(dep_m.group(1))
                if dep_id != epic_num:
                    deps.append(dep_id)

        epics.append(
            EpicSpec(
                id=epic_num,
                name=name,
                wp_ids=wp_ids,
                dependencies=deps,
                integration_contract=contract,
            )
        )
        i += 2

    # Only return if multiple epics (single epic = normal linear flow)
    if len(epics) < 2:
        return []

    return epics


# ---------------------------------------------------------------------------
# CURRENT-EPIC.md Generation — filtered context per epic
# ---------------------------------------------------------------------------


def generate_current_epic_md(
    epic: EpicSpec,
    wb_content: str,
    sflo_dir: str,
    completed_epics: list,
) -> str:
    """Generate CURRENT-EPIC.md content for a specific epic.

    This is what the dev agent reads instead of full WORK-BREAKDOWN.md.
    Contains:
    - Epic's work packages (filtered from full WB)
    - Prior epic outputs (integration contracts fulfilled)
    - Cross-cutting context from full WB
    - Full SCOPE.md reference (unchanged)

    Args:
        epic: The current epic spec
        wb_content: Full WORK-BREAKDOWN.md content
        sflo_dir: Pipeline state directory
        completed_epics: List of EpicResult for completed epics

    Returns:
        CURRENT-EPIC.md content string
    """
    import re

    lines = [
        f"# CURRENT-EPIC.md — Epic {epic.id}: {epic.name}",
        "",
        "## Scope",
        f"- Epic {epic.id} of total pipeline",
        f"- Work packages: {', '.join(f'WP-{wp}' for wp in epic.wp_ids)}",
        "",
    ]

    # Prior epic outputs (integration contracts fulfilled)
    if completed_epics:
        lines.append("## Prior Epic Outputs (available to you)")
        lines.append("")
        for prior in completed_epics:
            lines.append(f"### Epic {prior.epic_id} — COMPLETED")
            # Read prior epic's integration contract from state
            prior_contract_path = os.path.join(
                sflo_dir, f"EPIC-{prior.epic_id}-CONTRACT.md"
            )
            if os.path.isfile(prior_contract_path):
                with open(prior_contract_path, "r", encoding="utf-8") as f:
                    lines.append(f.read().strip())
            lines.append("")

    # Extract this epic's WP details from full WB
    lines.append("## Work Packages")
    lines.append("")
    for wp_id in epic.wp_ids:
        wp_section = _extract_wp_section(wb_content, wp_id)
        if wp_section:
            lines.append(wp_section)
            lines.append("")

    # Cross-cutting context (always include from WB)
    cross_cutting = _extract_section(wb_content, "Cross-Cutting Concern")
    if cross_cutting:
        lines.append("## Cross-Cutting Context")
        lines.append("")
        lines.append(cross_cutting)
        lines.append("")

    # Integration contract this epic must fulfill
    if epic.integration_contract:
        lines.append("## Your Integration Contract (MUST fulfill)")
        lines.append("")
        lines.append(epic.integration_contract)
        lines.append("")

    downstream_requirements = _extract_downstream_requirements(wb_content, epic.id)
    if downstream_requirements:
        lines.append("## Downstream Requirements (preserve compatibility)")
        lines.append("")
        lines.append(
            "Later epics explicitly require these outputs from this epic. Do not "
            "change or weaken them while implementing current WPs."
        )
        lines.append("")
        lines.extend(downstream_requirements)
        lines.append("")

    relevant_ac_coverage = _extract_relevant_ac_coverage(wb_content, epic.wp_ids)
    if relevant_ac_coverage:
        lines.append("## Relevant AC Coverage")
        lines.append("")
        lines.extend(relevant_ac_coverage)
        lines.append("")

    return "\n".join(lines)


def _extract_wp_section(wb_content: str, wp_id: int) -> str:
    """Extract a single WP section from WORK-BREAKDOWN.md."""
    import re

    # Match "### WP-N: ..." through next "### WP-" or "##"
    pattern = rf"###\s*WP-{wp_id}\s*:.*?(?=\n###\s*WP-|\n##\s[^#]|\Z)"
    match = re.search(pattern, wb_content, re.DOTALL)
    return match.group(0).strip() if match else f"### WP-{wp_id}: (not found in WB)"


def _extract_section(content: str, heading_fragment: str) -> str:
    """Extract a section by partial heading match."""
    import re

    pattern = rf"##\s*[^\n]*{re.escape(heading_fragment)}[^\n]*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_downstream_requirements(wb_content: str, epic_id: int) -> list:
    """Extract future epic requirements that reference the current epic."""
    import re

    requirements = []
    epic_blocks = re.finditer(
        r"###\s*Epic\s+(\d+)\s*:\s*(.*?)(?=\n###\s*Epic\s+\d+\s*:|\n##\s|\Z)",
        wb_content,
        re.DOTALL | re.IGNORECASE,
    )

    for block_match in epic_blocks:
        downstream_id = int(block_match.group(1))
        if downstream_id <= epic_id:
            continue

        block = block_match.group(2)
        name_match = re.match(r"([^\n]+)", block.strip())
        downstream_name = name_match.group(1).strip() if name_match else f"Epic {downstream_id}"
        requires_match = re.search(
            r"\*?\*?(?:Integration\s+Contract\s*[—\-]+\s*)?REQUIRES\s*:?\s*\*?\*?\s*\n(.*?)(?=\*\*|\n###|\Z)",
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if not requires_match:
            continue

        for line in re.findall(r"^-\s*(.+)$", requires_match.group(1), re.MULTILINE):
            if re.search(rf"\bEpic\s+{epic_id}\b", line, re.IGNORECASE):
                requirements.append(f"- Epic {downstream_id} ({downstream_name}) requires: {line.strip()}")

    return requirements


def _extract_relevant_ac_coverage(wb_content: str, wp_ids: list) -> list:
    """Extract AC coverage lines for WPs in the current epic."""
    import re

    coverage = _extract_section(wb_content, "AC Coverage")
    if not coverage:
        return []

    wp_pattern = "|".join(str(wp_id) for wp_id in wp_ids)
    relevant = []
    for line in coverage.splitlines():
        if re.search(rf"\bWP-({wp_pattern})\b", line):
            relevant.append(line.strip())
    return relevant


# ---------------------------------------------------------------------------
# State Extension — minimal epic tracking in state.json
# ---------------------------------------------------------------------------


def get_epic_state(state: dict) -> dict:
    """Read epic iteration state from pipeline state.

    Returns dict with keys: active, total_epics, current_epic, completed, failed.
    Returns empty dict if no epic iteration is active.
    """
    return state.get("epic_iteration", {})


def init_epic_state(state: dict, epics: list) -> None:
    """Initialize epic iteration state."""
    state["epic_iteration"] = {
        "active": True,
        "total_epics": len(epics),
        "current_epic": epics[0].id if epics else 0,
        "completed": [],
        "failed": [],
    }


def advance_epic_state(state: dict, epic_id: int, passed: bool) -> None:
    """Record epic completion and advance to next."""
    epic_state = state.get("epic_iteration", {})
    if passed:
        if epic_id not in epic_state.get("completed", []):
            epic_state.setdefault("completed", []).append(epic_id)
    else:
        if epic_id not in epic_state.get("failed", []):
            epic_state.setdefault("failed", []).append(epic_id)


def finalize_epic_state(state: dict) -> None:
    """Mark epic iteration as complete."""
    if "epic_iteration" in state:
        state["epic_iteration"]["active"] = False


# ---------------------------------------------------------------------------
# Run Gate Range — extracted from runner.py main loop
# ---------------------------------------------------------------------------


async def run_gate_range(
    start_gate: float,
    end_gate: float,
    sflo_dir: str,
    state: dict,
    adapter,
    *,
    user_prompt: str,
    output_dir: Optional[str] = None,
    runtime: Optional[str] = None,
    log: Callable,
    gates_config: dict,
    roles: dict,
    assignments: dict,
    max_iterations: int = 30,
) -> dict:
    """Execute gates [start_gate, end_gate] using normal machine.py loop.

    This is the core runner loop extracted to a reusable function.
    Machine.py is called normally — it has no knowledge of epics.
    State transitions happen as usual. Inner loops (QA reject -> dev rebuild)
    work identically.

    Returns when machine.py transitions past end_gate or escalates.

    Args:
        start_gate: First gate to execute (inclusive)
        end_gate: Last gate to execute (inclusive)
        sflo_dir: Pipeline state directory
        state: Current pipeline state (mutated in place)
        adapter: Runtime adapter for agent spawning
        user_prompt: Original user prompt
        output_dir: User deliverables directory
        runtime: Runtime identifier
        log: Logging function
        gates_config: GATES dict from constants
        roles: Roles config from pipeline.yaml
        assignments: Agent assignments from scout
        max_iterations: Safety limit

    Returns:
        dict with: passed (bool), final_gate (float), escalated (bool), reason (str)
    """
    # Import runner internals lazily to avoid circular imports
    from .machine import auto_transition, compute_next, apply_transition
    from .state import read_state, write_state

    iteration = 0
    last_gate_passed = None

    while iteration < max_iterations:
        iteration += 1

        auto_transition(state, sflo_dir, gates=gates_config)
        result = compute_next(state, sflo_dir, gates=gates_config)
        action = result.get("action")

        # Check if we've passed end_gate
        current_state = state.get("current_state", "")
        import re

        gate_match = re.match(r"(?:gate|check)-(\d+\.?\d*)", current_state)
        if gate_match:
            current_gate_num = float(gate_match.group(1))
            if current_gate_num > end_gate:
                # We've moved past end_gate — success
                return {
                    "passed": True,
                    "final_gate": last_gate_passed or end_gate,
                    "escalated": False,
                    "reason": "",
                }

        if action == "pipeline_complete":
            return {
                "passed": True,
                "final_gate": end_gate,
                "escalated": False,
                "reason": "pipeline_complete",
            }

        if action == "ask_human":
            return {
                "passed": False,
                "final_gate": last_gate_passed,
                "escalated": True,
                "reason": result.get("reason", "escalated"),
            }

        if action == "spawn_agent":
            # Delegate to runner's default_agent_runner
            from .runner import default_agent_runner

            agent = result["agent"]
            await default_agent_runner(
                agent,
                sflo_dir,
                output_dir,
                adapter=adapter,
                runtime=runtime,
                user_prompt=user_prompt,
                log=log,
            )

            # Validate and transition
            auto_transition(state, sflo_dir, gates=gates_config)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir, gates=gates_config)
            result = apply_transition(state, result, sflo_dir, gates=gates_config)
            state = read_state(sflo_dir)

            gate_num = result.get("gate")
            passed = result.get("pass", False)
            if passed and gate_num:
                last_gate_passed = gate_num
                log(f"    Epic gate {gate_num} ✓")
            elif not passed and gate_num:
                loop_action = result.get("action", "")
                if "loop" in loop_action:
                    retry_count = result.get("gate_retry_count")
                    retry_max = result.get("max")
                    log(f"    Epic gate {gate_num} ✗ — retry {retry_count}/{retry_max}")
                else:
                    log(f"    Epic gate {gate_num} ✗")

        elif action in ("validated", "check_failed"):
            gate_num = result.get("gate")
            if action == "validated":
                last_gate_passed = gate_num
                log(f"    Epic gate {gate_num} ✓ (existing)")
            else:
                log(f"    Epic gate {gate_num} ✗ (existing artifact failed)")
            result = apply_transition(state, result, sflo_dir, gates=gates_config)
            state = read_state(sflo_dir)

            if result.get("action") == "ask_human":
                return {
                    "passed": False,
                    "final_gate": last_gate_passed,
                    "escalated": True,
                    "reason": result.get("reason", "escalated"),
                }

        elif action == "spawn_parallel":
            # Run parallel gate entries sequentially inside an epic. This keeps
            # semantics identical (all artifacts must validate) while avoiding
            # duplicating runner.py's parallel orchestration in this extraction.
            from .runner import default_agent_runner
            from .state import read_state

            agents = result.get("agents", [])
            log(f"    Epic parallel gate — {len(agents)} agent(s)")
            for agent in agents:
                await default_agent_runner(
                    agent,
                    sflo_dir,
                    output_dir,
                    adapter=adapter,
                    runtime=runtime,
                    user_prompt=user_prompt,
                    log=log,
                )

            auto_transition(state, sflo_dir, gates=gates_config)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir, gates=gates_config)
            result = apply_transition(state, result, sflo_dir, gates=gates_config)
            state = read_state(sflo_dir)

            gate_num = result.get("gate")
            passed = result.get("pass", False)
            if passed and gate_num:
                last_gate_passed = gate_num
                log(f"    Epic gate {gate_num} ✓")
            elif not passed and gate_num:
                loop_action = result.get("action", "")
                if "loop" in loop_action:
                    retry_count = result.get("gate_retry_count")
                    retry_max = result.get("max")
                    log(f"    Epic gate {gate_num} ✗ — retry {retry_count}/{retry_max}")
                else:
                    log(f"    Epic gate {gate_num} ✗")

        else:
            log(f"    Unknown action in epic range: {action}")
            break

    # Safety limit hit
    return {
        "passed": False,
        "final_gate": last_gate_passed,
        "escalated": True,
        "reason": f"max iterations ({max_iterations}) in gate range",
    }


# ---------------------------------------------------------------------------
# Epic Iteration — the core loop
# ---------------------------------------------------------------------------


async def run_epic_iteration(
    epics: list,
    sflo_dir: str,
    state: dict,
    adapter,
    *,
    user_prompt: str,
    output_dir: Optional[str] = None,
    runtime: Optional[str] = None,
    log: Callable,
    gates_config: dict,
    roles: dict,
    assignments: dict,
    start_gate: float = 2,
    end_gate: float = 3,
) -> EpicIterationResult:
    """Execute gates [start_gate, end_gate] for each epic sequentially.

    This is the runner-level epic iteration from ADR Option C.
    For each epic:
      1. Write CURRENT-EPIC.md (filtered context)
      2. Run gate range [start_gate, end_gate] via normal machine.py
      3. Record epic completion
      4. Archive CURRENT-EPIC.md

    On failure: records which epic failed, continues to next (unless escalated).
    On crash recovery: reads epic_iteration state, skips completed epics.

    Args:
        epics: List of EpicSpec from detect_epics()
        sflo_dir: Pipeline state directory
        state: Pipeline state (mutated in place)
        adapter: Runtime adapter
        user_prompt: Original user prompt
        output_dir: User deliverables directory
        runtime: Runtime identifier
        log: Logging function
        gates_config: GATES dict
        roles: Roles config
        assignments: Agent assignments
        start_gate: First gate per epic (default: 2)
        end_gate: Last gate per epic (default: 3)

    Returns:
        EpicIterationResult with per-epic pass/fail
    """
    from .state import write_state

    # Read full WB content for CURRENT-EPIC.md generation
    wb_path = os.path.join(sflo_dir, "WORK-BREAKDOWN.md")
    wb_content = ""
    if os.path.isfile(wb_path):
        with open(wb_path, "r", encoding="utf-8") as f:
            wb_content = f.read()

    # Initialize or resume epic state
    epic_state = get_epic_state(state)
    if not epic_state.get("active"):
        init_epic_state(state, epics)
        write_state(sflo_dir, state)
        epic_state = get_epic_state(state)

    completed_ids = set(epic_state.get("completed", []))
    epic_results = []

    log(f"  Epic iteration: {len(epics)} epics, gates {start_gate}-{end_gate}")

    for epic in epics:
        # Skip already completed epics (crash recovery)
        if epic.id in completed_ids:
            log(f"  Epic {epic.id} ({epic.name}) — already completed, skipping")
            epic_results.append(EpicResult(epic_id=epic.id, passed=True))
            continue

        log(f"  Epic {epic.id}/{len(epics)}: {epic.name} [{len(epic.wp_ids)} WPs]")

        # Update current_epic in state
        state["epic_iteration"]["current_epic"] = epic.id
        write_state(sflo_dir, state)

        # Generate CURRENT-EPIC.md
        completed_results = [r for r in epic_results if r.passed]
        current_epic_content = generate_current_epic_md(
            epic, wb_content, sflo_dir, completed_results
        )
        current_epic_path = os.path.join(sflo_dir, "CURRENT-EPIC.md")
        with open(current_epic_path, "w", encoding="utf-8") as f:
            f.write(current_epic_content)
        log(f"    CURRENT-EPIC.md written ({len(current_epic_content)} chars)")

        # Reset state to start_gate for this epic
        state["current_state"] = f"gate-{start_gate}"
        # Clear any prior gate statuses for the range we're about to run
        for gate_key in list(state.get("gates", {}).keys()):
            try:
                gk = float(gate_key)
                if start_gate <= gk <= end_gate:
                    state["gates"][gate_key]["status"] = "waiting"
            except (ValueError, KeyError):
                pass
        # Reset inner loop counter for this epic
        state["inner_loops"] = 0
        write_state(sflo_dir, state)

        # Clean prior gate artifacts for this range
        for gate_key, gate_info in gates_config.items():
            if start_gate <= gate_key <= end_gate:
                if isinstance(gate_info, list):
                    for entry in gate_info:
                        artifact = entry.get("artifact")
                        if artifact:
                            path = os.path.join(sflo_dir, artifact)
                            if os.path.isfile(path):
                                os.remove(path)
                else:
                    artifact = gate_info.get("artifact")
                    if artifact:
                        path = os.path.join(sflo_dir, artifact)
                        if os.path.isfile(path):
                            os.remove(path)

        # Run gate range for this epic
        range_result = await run_gate_range(
            start_gate=start_gate,
            end_gate=end_gate,
            sflo_dir=sflo_dir,
            state=state,
            adapter=adapter,
            user_prompt=user_prompt,
            output_dir=output_dir,
            runtime=runtime,
            log=log,
            gates_config=gates_config,
            roles=roles,
            assignments=assignments,
        )

        epic_passed = range_result["passed"]
        epic_result = EpicResult(
            epic_id=epic.id,
            passed=epic_passed,
            gate_results=range_result,
            error=range_result.get("reason", "") if not epic_passed else "",
        )
        epic_results.append(epic_result)

        # Update state
        advance_epic_state(state, epic.id, epic_passed)
        write_state(sflo_dir, state)

        if epic_passed:
            log(f"  Epic {epic.id} ✓ — completed")
            # Archive CURRENT-EPIC.md as EPIC-N-CONTRACT.md for future epics
            contract_path = os.path.join(sflo_dir, f"EPIC-{epic.id}-CONTRACT.md")
            if epic.integration_contract:
                with open(contract_path, "w", encoding="utf-8") as f:
                    f.write(
                        f"# Epic {epic.id}: {epic.name} — Integration Contract\n\n"
                        f"{epic.integration_contract}\n"
                    )
        else:
            log(f"  Epic {epic.id} ✗ — failed: {range_result.get('reason', '')}")
            if range_result.get("escalated"):
                # Stop iteration on escalation
                finalize_epic_state(state)
                write_state(sflo_dir, state)
                return EpicIterationResult(
                    all_passed=False,
                    epic_results=epic_results,
                    failed_epics=[epic.id],
                    escalated=True,
                    escalation_reason=range_result.get("reason", ""),
                )

    # All epics processed
    finalize_epic_state(state)
    write_state(sflo_dir, state)

    failed_ids = [r.epic_id for r in epic_results if not r.passed]

    # Clean up CURRENT-EPIC.md
    current_epic_path = os.path.join(sflo_dir, "CURRENT-EPIC.md")
    if os.path.isfile(current_epic_path):
        os.remove(current_epic_path)

    return EpicIterationResult(
        all_passed=len(failed_ids) == 0,
        epic_results=epic_results,
        failed_epics=failed_ids,
        escalated=False,
    )
