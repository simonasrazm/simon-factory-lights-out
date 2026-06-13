"""Tests for sflo.src.epic_orchestrator — runner-level epic iteration."""

import os
import sys
import json
import tempfile
import pytest

# Allow import from sibling dir
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from epic_orchestrator import (
    EpicSpec,
    EpicResult,
    EpicIterationResult,
    detect_epics,
    parse_epic_groups,
    generate_current_epic_md,
    _extract_downstream_requirements,
    _extract_relevant_ac_coverage,
    get_epic_state,
    init_epic_state,
    advance_epic_state,
    finalize_epic_state,
)


# --- Test Fixtures ---

SAMPLE_WB = """# WORK-BREAKDOWN.md

## Meta
- Source: SCOPE.md
- Complexity: L
- Total WPs: 8
- Epic count: 3
- Critical path: WP-1 → WP-2 → WP-5 → WP-7 (4 steps)

## Work Packages

### WP-1: DB schema + tenant isolation [Effort: M]
- **Type**: infrastructure
- **Inputs**: SCOPE AC1, AC2
- **Outputs**: migrations/, tenant model
- **Acceptance**: tenant isolation test passes
- **Dependencies**: none
- **Dev notes**: Use RLS for Postgres

### WP-2: Event ingestion pipeline [Effort: M]
- **Type**: feature
- **Inputs**: SCOPE AC3, AC4; WP-1 schema
- **Outputs**: ingestion service, queue
- **Acceptance**: events ingested at 1k/s
- **Dependencies**: WP-1

### WP-3: RBAC middleware [Effort: S]
- **Type**: cross-cutting
- **Inputs**: SCOPE AC7, AC8
- **Outputs**: auth middleware, role model
- **Acceptance**: role-based access verified
- **Dependencies**: none

### WP-4: Query engine [Effort: M]
- **Type**: feature
- **Inputs**: SCOPE AC5, AC6; WP-1 schema, WP-3 RBAC
- **Outputs**: query API
- **Acceptance**: aggregation queries correct
- **Dependencies**: WP-1, WP-3

### WP-5: Widget framework [Effort: M]
- **Type**: feature
- **Inputs**: WP-2, WP-4
- **Outputs**: widget base classes
- **Acceptance**: widget renders data
- **Dependencies**: WP-2, WP-4

### WP-6: Widget types [Effort: M]
- **Type**: feature
- **Inputs**: WP-5
- **Outputs**: chart, table, KPI widgets
- **Acceptance**: all widget types render
- **Dependencies**: WP-5

### WP-7: Dashboard composition [Effort: M]
- **Type**: feature
- **Inputs**: WP-3, WP-5, WP-6
- **Outputs**: dashboard builder
- **Acceptance**: multi-widget dashboard works
- **Dependencies**: WP-3, WP-5, WP-6

### WP-8: Integration tests [Effort: S]
- **Type**: quality
- **Inputs**: WP-1 through WP-7
- **Outputs**: test suite
- **Acceptance**: all integration tests pass
- **Dependencies**: WP-1, WP-2, WP-3, WP-4, WP-5, WP-6, WP-7

## Epic Groups

### Epic 1: Foundation
**WPs**: WP-1, WP-2, WP-3
**Context**: Core infrastructure and cross-cutting auth
**Integration Contract — PROVIDES:**
- Tenant-isolated DB schema with RLS
- Event ingestion queue interface
- RBAC middleware for all downstream services
**Integration Contract — REQUIRES:**
- None (root epic)

### Epic 2: Data & Widgets
**WPs**: WP-4, WP-5, WP-6
**Context**: Query engine and widget rendering
**Integration Contract — PROVIDES:**
- Query aggregation API
- Widget framework with chart/table/KPI types
**Integration Contract — REQUIRES:**
- Epic 1: DB schema, RBAC middleware, ingestion queue

### Epic 3: Composition & Quality
**WPs**: WP-7, WP-8
**Context**: Dashboard assembly and integration verification
**Integration Contract — PROVIDES:**
- Complete dashboard builder
- Verified integration test suite
**Integration Contract — REQUIRES:**
- Epic 1: RBAC middleware
- Epic 2: Widget framework, widget types

## Dependency DAG
WP-1 → WP-2 → WP-5 → WP-6 → WP-7
WP-1 → WP-4 → WP-5
WP-3 → WP-4
WP-3 → WP-7

## Critical Path
WP-1 → WP-2 → WP-5 → WP-6 → WP-7 → WP-8

## Cross-Cutting Concern Strategy
- **Auth/RBAC**: Resolved in WP-3 (Epic 1). All downstream WPs import middleware.
- **Tenant isolation**: Resolved in WP-1. RLS policy propagates to all queries.

## AC Coverage
- AC1 → WP-1
- AC2 → WP-1
- AC3 → WP-2
- AC4 → WP-2
- AC5 → WP-4
- AC6 → WP-4
- AC7 → WP-3
- AC8 → WP-3
"""

SIMPLE_WB_ONE_EPIC = """# WORK-BREAKDOWN.md

## Meta
- Complexity: M
- Total WPs: 3
- Epic count: 1

## Work Packages

### WP-1: Schema [Effort: S]
- **Dependencies**: none

### WP-2: API [Effort: M]
- **Dependencies**: WP-1

### WP-3: UI [Effort: M]
- **Dependencies**: WP-2

## Epic Groups

### Epic 1: Everything
**WPs**: WP-1, WP-2, WP-3
**Context**: Single epic for M-scope
"""


# --- Parsing Tests ---


class TestParseEpicGroups:
    def test_parses_three_epics(self):
        epics = parse_epic_groups(SAMPLE_WB)
        assert len(epics) == 3

    def test_epic_ids(self):
        epics = parse_epic_groups(SAMPLE_WB)
        assert [e.id for e in epics] == [1, 2, 3]

    def test_epic_names(self):
        epics = parse_epic_groups(SAMPLE_WB)
        assert epics[0].name == "Foundation"
        assert epics[1].name == "Data & Widgets"
        assert epics[2].name == "Composition & Quality"

    def test_wp_ids(self):
        epics = parse_epic_groups(SAMPLE_WB)
        assert epics[0].wp_ids == [1, 2, 3]
        assert epics[1].wp_ids == [4, 5, 6]
        assert epics[2].wp_ids == [7, 8]

    def test_dependencies(self):
        epics = parse_epic_groups(SAMPLE_WB)
        assert epics[0].dependencies == []  # Root epic
        assert 1 in epics[1].dependencies  # Epic 2 requires Epic 1
        assert 1 in epics[2].dependencies  # Epic 3 requires Epic 1
        assert 2 in epics[2].dependencies  # Epic 3 requires Epic 2

    def test_integration_contract(self):
        epics = parse_epic_groups(SAMPLE_WB)
        assert "Tenant-isolated DB schema" in epics[0].integration_contract
        assert "RBAC middleware" in epics[0].integration_contract

    def test_single_epic_returns_empty(self):
        epics = parse_epic_groups(SIMPLE_WB_ONE_EPIC)
        assert epics == []  # Single epic = use linear flow

    def test_no_epic_section_returns_empty(self):
        epics = parse_epic_groups("# Just a document\n\nNo epics here.\n")
        assert epics == []


class TestDetectEpics:
    def test_detects_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            wb_path = os.path.join(td, "WORK-BREAKDOWN.md")
            with open(wb_path, "w") as f:
                f.write(SAMPLE_WB)
            epics = detect_epics(td)
            assert len(epics) == 3

    def test_no_wb_file(self):
        with tempfile.TemporaryDirectory() as td:
            epics = detect_epics(td)
            assert epics == []

    def test_single_epic_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            wb_path = os.path.join(td, "WORK-BREAKDOWN.md")
            with open(wb_path, "w") as f:
                f.write(SIMPLE_WB_ONE_EPIC)
            epics = detect_epics(td)
            assert epics == []


# --- CURRENT-EPIC.md Generation Tests ---


class TestGenerateCurrentEpicMd:
    def test_contains_epic_header(self):
        epic = EpicSpec(id=1, name="Foundation", wp_ids=[1, 2, 3])
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])
        assert "Epic 1: Foundation" in content

    def test_contains_wp_sections(self):
        epic = EpicSpec(id=1, name="Foundation", wp_ids=[1, 2, 3])
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])
        assert "WP-1:" in content
        assert "WP-2:" in content
        assert "WP-3:" in content
        # Should NOT contain WPs from other epics
        assert "WP-4:" not in content
        assert "WP-5:" not in content

    def test_contains_cross_cutting(self):
        epic = EpicSpec(id=2, name="Data", wp_ids=[4, 5, 6])
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])
        assert "Cross-Cutting" in content
        assert "RBAC" in content

    def test_contains_integration_contract(self):
        epic = EpicSpec(
            id=1,
            name="Foundation",
            wp_ids=[1, 2, 3],
            integration_contract="Provides DB schema with RLS",
        )
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])
        assert "Integration Contract" in content
        assert "Provides DB schema" in content

    def test_includes_prior_epic_outputs(self):
        epic = EpicSpec(id=2, name="Data", wp_ids=[4, 5, 6])
        prior = [EpicResult(epic_id=1, passed=True)]

        with tempfile.TemporaryDirectory() as td:
            # Write a contract file for epic 1
            contract = os.path.join(td, "EPIC-1-CONTRACT.md")
            with open(contract, "w") as f:
                f.write("DB schema ready with RLS")

            content = generate_current_epic_md(epic, SAMPLE_WB, td, prior)
            assert "Prior Epic Outputs" in content
            assert "DB schema ready" in content

    def test_includes_downstream_requirements(self):
        epic = EpicSpec(id=1, name="Foundation", wp_ids=[1, 2, 3])
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])

        assert "Downstream Requirements" in content
        assert "Epic 2 (Data & Widgets) requires" in content
        assert "DB schema, RBAC middleware, ingestion queue" in content
        assert "Epic 3 (Composition & Quality) requires" in content
        assert "RBAC middleware" in content

    def test_downstream_requirements_keep_current_epic_lean(self):
        epic = EpicSpec(id=2, name="Data & Widgets", wp_ids=[4, 5, 6])
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])

        assert "Epic 3 (Composition & Quality) requires" in content
        assert "Widget framework, widget types" in content
        assert "Epic 1:" not in "\n".join(
            line for line in content.splitlines() if "requires" in line
        )

    def test_includes_relevant_ac_coverage_only(self):
        epic = EpicSpec(id=2, name="Data & Widgets", wp_ids=[4, 5, 6])
        content = generate_current_epic_md(epic, SAMPLE_WB, "/tmp/test", [])

        assert "Relevant AC Coverage" in content
        assert "AC5 → WP-4" in content
        assert "AC6 → WP-4" in content
        assert "AC1 → WP-1" not in content


class TestR3H2ContextFilteringSafety:
    """R3-H2: CURRENT-EPIC.md contains enough context without full WB noise."""

    def test_current_epic_has_all_required_context_for_epic_2(self):
        epic = EpicSpec(
            id=2,
            name="Data & Widgets",
            wp_ids=[4, 5, 6],
            dependencies=[1],
            integration_contract="- Query aggregation API\n- Widget framework with chart/table/KPI types",
        )
        prior = [EpicResult(epic_id=1, passed=True)]

        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "EPIC-1-CONTRACT.md"), "w") as f:
                f.write("RBAC middleware for all downstream services")

            content = generate_current_epic_md(epic, SAMPLE_WB, td, prior)

        assert "WP-4:" in content
        assert "WP-5:" in content
        assert "WP-6:" in content
        assert "WP-1:" not in content
        assert "RBAC middleware for all downstream services" in content
        assert "Cross-Cutting Context" in content
        assert "Your Integration Contract" in content
        assert "Downstream Requirements" in content
        assert "Relevant AC Coverage" in content

    def test_extract_downstream_requirements_for_epic_1(self):
        requirements = _extract_downstream_requirements(SAMPLE_WB, 1)

        assert len(requirements) == 2
        assert any("Epic 2" in req for req in requirements)
        assert any("Epic 3" in req for req in requirements)

    def test_extract_relevant_ac_coverage(self):
        coverage = _extract_relevant_ac_coverage(SAMPLE_WB, [4, 5, 6])

        assert coverage == ["- AC5 → WP-4", "- AC6 → WP-4"]


# --- State Management Tests ---


class TestEpicState:
    def test_init_epic_state(self):
        state = {"current_state": "gate-2"}
        epics = [
            EpicSpec(id=1, name="A", wp_ids=[1, 2]),
            EpicSpec(id=2, name="B", wp_ids=[3, 4]),
        ]
        init_epic_state(state, epics)

        es = get_epic_state(state)
        assert es["active"] is True
        assert es["total_epics"] == 2
        assert es["current_epic"] == 1
        assert es["completed"] == []
        assert es["failed"] == []

    def test_advance_epic_passed(self):
        state = {
            "epic_iteration": {
                "active": True,
                "total_epics": 3,
                "current_epic": 1,
                "completed": [],
                "failed": [],
            }
        }
        advance_epic_state(state, 1, passed=True)
        assert 1 in state["epic_iteration"]["completed"]
        assert 1 not in state["epic_iteration"]["failed"]

    def test_advance_epic_failed(self):
        state = {
            "epic_iteration": {
                "active": True,
                "total_epics": 3,
                "current_epic": 2,
                "completed": [1],
                "failed": [],
            }
        }
        advance_epic_state(state, 2, passed=False)
        assert 2 in state["epic_iteration"]["failed"]
        assert 2 not in state["epic_iteration"]["completed"]

    def test_advance_no_duplicate(self):
        state = {
            "epic_iteration": {
                "active": True,
                "total_epics": 3,
                "current_epic": 1,
                "completed": [1],
                "failed": [],
            }
        }
        advance_epic_state(state, 1, passed=True)
        assert state["epic_iteration"]["completed"].count(1) == 1

    def test_finalize(self):
        state = {
            "epic_iteration": {
                "active": True,
                "total_epics": 2,
                "current_epic": 2,
                "completed": [1, 2],
                "failed": [],
            }
        }
        finalize_epic_state(state)
        assert state["epic_iteration"]["active"] is False

    def test_get_epic_state_empty(self):
        state = {"current_state": "gate-1"}
        assert get_epic_state(state) == {}


# --- WP Section Extraction Tests ---


class TestWpExtraction:
    def test_extract_wp_section(self):
        from epic_orchestrator import _extract_wp_section

        section = _extract_wp_section(SAMPLE_WB, 1)
        assert "DB schema + tenant isolation" in section
        assert "Use RLS for Postgres" in section

    def test_extract_wp_not_found(self):
        from epic_orchestrator import _extract_wp_section

        section = _extract_wp_section(SAMPLE_WB, 99)
        assert "not found" in section

    def test_extract_cross_cutting_section(self):
        from epic_orchestrator import _extract_section

        section = _extract_section(SAMPLE_WB, "Cross-Cutting Concern")
        assert "Auth/RBAC" in section
        assert "Tenant isolation" in section


# --- Integration Test (no actual agent spawning) ---


class TestEpicIterationResult:
    def test_all_passed(self):
        result = EpicIterationResult(
            all_passed=True,
            epic_results=[
                EpicResult(epic_id=1, passed=True),
                EpicResult(epic_id=2, passed=True),
            ],
            failed_epics=[],
        )
        assert result.all_passed is True
        assert len(result.epic_results) == 2
        assert result.failed_epics == []

    def test_partial_failure(self):
        result = EpicIterationResult(
            all_passed=False,
            epic_results=[
                EpicResult(epic_id=1, passed=True),
                EpicResult(epic_id=2, passed=False, error="QA reject"),
            ],
            failed_epics=[2],
        )
        assert result.all_passed is False
        assert result.failed_epics == [2]

    def test_escalated(self):
        result = EpicIterationResult(
            all_passed=False,
            epic_results=[EpicResult(epic_id=1, passed=False)],
            failed_epics=[1],
            escalated=True,
            escalation_reason="inner loop max",
        )
        assert result.escalated is True
        assert "inner loop" in result.escalation_reason
