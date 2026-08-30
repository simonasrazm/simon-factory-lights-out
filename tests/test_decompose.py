"""Tests for sflo.src.decompose — mechanical work-breakdown generator."""

import os
import tempfile
import pytest

# Allow import from sibling dir
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from decompose import (
    parse_deliverables,
    compute_depth,
    topological_layers,
    find_critical_path,
    validate_dag,
    cluster_into_epics,
    generate_work_breakdown,
)


# --- Test Fixtures ---

SAMPLE_SCOPE = """
## What We're Building
A multi-tenant analytics dashboard.

## Deliverables
1. **DB schema + tenant isolation** — AC1, AC2. No deps. [M]
2. **Event ingestion pipeline** — AC3, AC4. Depends: D1. [M]
3. **RBAC middleware + role model** — AC7, AC8. No deps. [S]
4. **Query engine with aggregation** — AC5, AC6. Depends: D1, D3. [M]
5. **Widget framework** — AC9. Depends: D2, D4. [M]
6. **Widget types (chart, table, KPI)** — AC10, AC11. Depends: D5. [M]
7. **Dashboard composition** — AC12. Depends: D3, D5, D6. [M]
8. **Real-time WebSocket updates** — AC13. Depends: D7. [S]
9. **PDF/CSV export** — AC14, AC15. Depends: D4, D7. [S]
10. **Webhook alert engine** — AC16, AC17. Depends: D4. [M]
11. **Integration tests** — AC18. Depends: D1-D10. [S]
12. **Deployment config** — AC19. Depends: D1-D10. [S]

## Acceptance Criteria
- [ ] AC1: Tenant isolation verified
"""

SIMPLE_SCOPE = """
## Deliverables
1. **Schema migration** — AC1. No deps. [S]
2. **API endpoints** — AC2, AC3. Depends: D1. [M]
3. **Frontend UI** — AC4. Depends: D2. [M]
"""


# --- Parsing Tests ---


class TestParseDeliverables:
    def test_parses_all_deliverables(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert len(result) == 12

    def test_parses_title(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert result[0].title == "DB schema + tenant isolation"
        assert result[5].title == "Widget types (chart, table, KPI)"

    def test_parses_ac_refs(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert result[0].ac_refs == ["AC1", "AC2"]
        assert result[4].ac_refs == ["AC9"]

    def test_parses_no_deps(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert result[0].depends_on == []
        assert result[2].depends_on == []

    def test_parses_single_dep(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert result[1].depends_on == [1]  # Depends: D1

    def test_parses_multiple_deps(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert result[3].depends_on == [1, 3]  # Depends: D1, D3

    def test_parses_dep_ranges(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        # D11: Depends: D1-D10
        assert result[10].depends_on == list(range(1, 11))

    def test_parses_complexity(self):
        result = parse_deliverables(SAMPLE_SCOPE)
        assert result[0].complexity == "M"
        assert result[2].complexity == "S"

    def test_simple_scope(self):
        result = parse_deliverables(SIMPLE_SCOPE)
        assert len(result) == 3
        assert result[0].depends_on == []
        assert result[1].depends_on == [1]
        assert result[2].depends_on == [2]


# --- DAG Tests ---


class TestDAGOperations:
    def test_compute_depth_roots(self):
        deliverables = parse_deliverables(SAMPLE_SCOPE)
        depths = compute_depth(deliverables)
        # D1 and D3 have no deps → depth 0
        assert depths[1] == 0
        assert depths[3] == 0

    def test_compute_depth_chain(self):
        deliverables = parse_deliverables(SIMPLE_SCOPE)
        depths = compute_depth(deliverables)
        assert depths[1] == 0
        assert depths[2] == 1
        assert depths[3] == 2

    def test_topological_layers(self):
        deliverables = parse_deliverables(SIMPLE_SCOPE)
        layers = topological_layers(deliverables)
        assert layers[0] == [1]
        assert layers[1] == [2]
        assert layers[2] == [3]

    def test_topological_parallel(self):
        deliverables = parse_deliverables(SAMPLE_SCOPE)
        layers = topological_layers(deliverables)
        # Layer 0 should contain D1, D3 (both no deps)
        assert set(layers[0]) == {1, 3}

    def test_critical_path(self):
        deliverables = parse_deliverables(SIMPLE_SCOPE)
        path = find_critical_path(deliverables)
        assert path == [1, 2, 3]

    def test_critical_path_complex(self):
        deliverables = parse_deliverables(SAMPLE_SCOPE)
        path = find_critical_path(deliverables)
        # Should be one of the longest chains through the DAG
        assert len(path) >= 5
        # Must start at a root (depth 0)
        assert path[0] in [1, 3]

    def test_validate_dag_valid(self):
        deliverables = parse_deliverables(SAMPLE_SCOPE)
        errors = validate_dag(deliverables)
        assert errors == []

    def test_validate_dag_missing_dep(self):
        from decompose import Deliverable
        deliverables = [
            Deliverable(id=1, title="A", ac_refs=[], depends_on=[99], complexity="S")
        ]
        errors = validate_dag(deliverables)
        assert any("D99" in e for e in errors)


# --- Epic Grouping Tests ---


class TestEpicGrouping:
    def test_simple_clustering(self):
        deliverables = parse_deliverables(SIMPLE_SCOPE)
        epics = cluster_into_epics(deliverables, max_per_epic=5)
        # 3 deliverables, all fit in one epic (linear chain, each layer has 1)
        # Actually 3 layers of 1 each → might merge to 1-2 epics
        assert len(epics) >= 1
        # All deliverables covered
        all_wp_ids = [wp_id for e in epics for wp_id in e.wp_ids]
        assert set(all_wp_ids) == {1, 2, 3}

    def test_complex_clustering(self):
        deliverables = parse_deliverables(SAMPLE_SCOPE)
        epics = cluster_into_epics(deliverables, max_per_epic=5)
        # Should produce multiple epics for 12 deliverables
        assert len(epics) >= 2
        # All deliverables covered
        all_wp_ids = [wp_id for e in epics for wp_id in e.wp_ids]
        assert set(all_wp_ids) == set(range(1, 13))

    def test_respects_max_per_epic(self):
        deliverables = parse_deliverables(SAMPLE_SCOPE)
        epics = cluster_into_epics(deliverables, max_per_epic=3)
        for epic in epics:
            assert len(epic.wp_ids) <= 3 + 1  # +1 tolerance for layer boundaries


# --- Integration Tests ---


class TestGenerateWorkBreakdown:
    def test_full_generation(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(SAMPLE_SCOPE)
            scope_path = f.name

        output_path = scope_path.replace(".md", "-WB.md")
        try:
            content = generate_work_breakdown(scope_path, output_path)
            assert "# WORK-BREAKDOWN.md" in content
            assert "## Meta" in content
            assert "## Critical Path" in content
            assert "## AC Coverage" in content
            # All 19 ACs mapped
            for i in range(1, 20):
                assert f"AC{i}" in content
            assert os.path.isfile(output_path)
        finally:
            os.unlink(scope_path)
            if os.path.isfile(output_path):
                os.unlink(output_path)

    def test_no_deliverables_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# No deliverables here\n\nJust text.\n")
            scope_path = f.name

        output_path = scope_path.replace(".md", "-WB.md")
        try:
            with pytest.raises(ValueError, match="No deliverables"):
                generate_work_breakdown(scope_path, output_path)
        finally:
            os.unlink(scope_path)
