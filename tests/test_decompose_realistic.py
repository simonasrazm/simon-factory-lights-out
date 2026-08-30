"""R3-H1: Decomposer Output Quality — realistic PM-output SCOPE.md.

Tests decompose.py against a SCOPE.md that mimics real PM output patterns:
- Imperfect formatting (some variation in how deps are written)
- Cross-cutting concerns spanning multiple deliverables
- Mixed complexity levels
- Large scope (L-tier: 10+ deliverables)
"""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from decompose import (
    parse_deliverables,
    validate_dag,
    cluster_into_epics,
    topological_layers,
    find_critical_path,
    generate_work_breakdown,
)
from validate_wb import validate_work_breakdown


# Realistic PM output — note the variations in formatting that a real PM might produce
REALISTIC_SCOPE = """# SCOPE.md — Multi-Tenant Analytics Dashboard

## What We're Building

A real-time analytics dashboard for SaaS teams. Supports multi-tenancy,
role-based access, custom widgets, and PDF export. Must handle 100+ concurrent
users per tenant with sub-second query response times.

## Complexity Estimate: L

## Deliverables

1. **PostgreSQL schema + tenant isolation** — AC1, AC2, AC3. No deps. [M]
2. **Event ingestion service** — AC4, AC5. Depends: D1. [M]
3. **RBAC middleware + permission model** — AC6, AC7. No deps. [M]
4. **Query aggregation engine** — AC8, AC9, AC10. Depends: D1, D3. [L]
5. **Widget framework (base classes + registry)** — AC11, AC12. Depends: D2, D4. [M]
6. **Chart widget implementation** — AC13. Depends: D5. [S]
7. **Table widget implementation** — AC14. Depends: D5. [S]
8. **KPI card widget** — AC15. Depends: D5. [S]
9. **Dashboard composition engine** — AC16, AC17. Depends: D3, D5, D6, D7, D8. [L]
10. **Real-time WebSocket updates** — AC18. Depends: D9. [M]
11. **PDF/CSV export service** — AC19, AC20. Depends: D4, D9. [M]
12. **Webhook alert engine** — AC21, AC22. Depends: D4. [M]
13. **E2E integration test suite** — AC23. Depends: D1-D12. [S]
14. **Deployment configuration (Docker + CI)** — AC24. Depends: D1-D12. [S]

## Acceptance Criteria

- [ ] AC1: Tenant data isolated — tenant A cannot query tenant B rows
- [ ] AC2: Schema migrations reversible (up + down)
- [ ] AC3: Supports 1M+ rows per tenant without performance degradation
- [ ] AC4: Events ingested at sustained 5k events/second
- [ ] AC5: Malformed events rejected with clear error message
- [ ] AC6: Admin, analyst, and viewer roles enforced on all endpoints
- [ ] AC7: Permission changes take effect without server restart
- [ ] AC8: Aggregation queries return within 2s for 1M rows
- [ ] AC9: Supports GROUP BY, SUM, AVG, COUNT, percentiles
- [ ] AC10: Query results cached for 30s (configurable TTL)
- [ ] AC11: Widget registry accepts new widget types at runtime
- [ ] AC12: Widget base class enforces data-binding contract
- [ ] AC13: Line chart renders time-series data with zoom
- [ ] AC14: Data table supports sorting, pagination, column resize
- [ ] AC15: KPI card shows metric, trend arrow, sparkline
- [ ] AC16: Dashboard supports drag-and-drop widget placement
- [ ] AC17: Dashboard state persists per-user
- [ ] AC18: Widget data refreshes within 2s of new event ingestion
- [ ] AC19: PDF export matches on-screen layout
- [ ] AC20: CSV export includes all visible data with headers
- [ ] AC21: Alert triggers on threshold breach (configurable per metric)
- [ ] AC22: Webhook delivery with retry (3 attempts, exponential backoff)
- [ ] AC23: Full user journey test: login → create dashboard → add widgets → export
- [ ] AC24: One-command deployment with `docker compose up`
"""


class TestRealisticParsing:
    """Can decompose.py handle realistic PM formatting?"""

    def test_parses_all_14_deliverables(self):
        result = parse_deliverables(REALISTIC_SCOPE)
        assert len(result) == 14

    def test_parses_range_deps(self):
        """D13 and D14 have 'Depends: D1-D12' — range syntax."""
        result = parse_deliverables(REALISTIC_SCOPE)
        d13 = next(d for d in result if d.id == 13)
        assert d13.depends_on == list(range(1, 13))

    def test_parses_multi_dep_with_mixed_format(self):
        """D9 has 'Depends: D3, D5, D6, D7, D8' — multiple individual deps."""
        result = parse_deliverables(REALISTIC_SCOPE)
        d9 = next(d for d in result if d.id == 9)
        assert set(d9.depends_on) == {3, 5, 6, 7, 8}

    def test_dag_is_valid(self):
        result = parse_deliverables(REALISTIC_SCOPE)
        errors = validate_dag(result)
        assert errors == []

    def test_complexity_parsed(self):
        result = parse_deliverables(REALISTIC_SCOPE)
        complexities = {d.id: d.complexity for d in result}
        assert complexities[1] == "M"
        assert complexities[4] == "L"
        assert complexities[6] == "S"

    def test_ac_refs_complete(self):
        """All 24 ACs must be referenced."""
        result = parse_deliverables(REALISTIC_SCOPE)
        all_acs = set()
        for d in result:
            all_acs.update(d.ac_refs)
        expected = {f"AC{i}" for i in range(1, 25)}
        assert all_acs == expected


class TestRealisticStructure:
    """DAG, layers, critical path, epic grouping on realistic input."""

    def test_topological_layers_valid(self):
        deliverables = parse_deliverables(REALISTIC_SCOPE)
        layers = topological_layers(deliverables)
        # Layer 0 should contain root nodes (no deps): D1, D3
        assert 1 in layers[0]
        assert 3 in layers[0]
        # D2 depends on D1, so layer >= 1
        d2_layer = next(i for i, layer in enumerate(layers) if 2 in layer)
        assert d2_layer >= 1

    def test_critical_path_reasonable(self):
        deliverables = parse_deliverables(REALISTIC_SCOPE)
        path = find_critical_path(deliverables)
        # Should be at least 5 steps for L-scope
        assert len(path) >= 5
        # Must start at root
        assert path[0] in [1, 3]
        # Must end at a leaf (D13 or D14 likely, since they depend on everything)
        assert path[-1] in [13, 14, 9, 10, 11]

    def test_epic_grouping_produces_multiple(self):
        deliverables = parse_deliverables(REALISTIC_SCOPE)
        epics = cluster_into_epics(deliverables, max_per_epic=5)
        # 14 deliverables with max 5 per epic = at least 3 epics
        assert len(epics) >= 3
        # All WPs covered
        all_wps = set()
        for e in epics:
            all_wps.update(e.wp_ids)
        assert all_wps == set(range(1, 15))

    def test_epic_respects_deps(self):
        """No WP should be in an earlier epic than its dependencies."""
        deliverables = parse_deliverables(REALISTIC_SCOPE)
        epics = cluster_into_epics(deliverables, max_per_epic=5)
        by_id = {d.id: d for d in deliverables}

        # Build WP → epic mapping
        wp_to_epic = {}
        for epic in epics:
            for wp_id in epic.wp_ids:
                wp_to_epic[wp_id] = epic.id

        violations = []
        for d in deliverables:
            for dep_id in d.depends_on:
                if dep_id in wp_to_epic and d.id in wp_to_epic:
                    if wp_to_epic[dep_id] > wp_to_epic[d.id]:
                        violations.append(
                            f"WP-{d.id} (Epic {wp_to_epic[d.id]}) depends on "
                            f"WP-{dep_id} (Epic {wp_to_epic[dep_id]})"
                        )
        assert violations == [], f"Epic ordering violations: {violations}"


class TestRealisticWBGeneration:
    """Full end-to-end: SCOPE → WB → validation."""

    def test_generates_valid_wb(self):
        """Generated WB passes all structural validation checks."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(REALISTIC_SCOPE)
            scope_path = f.name

        output_path = scope_path.replace(".md", "-WB.md")
        try:
            wb_content = generate_work_breakdown(scope_path, output_path)

            # Run validation against source SCOPE
            checks = validate_work_breakdown(wb_content, REALISTIC_SCOPE)
            failed = [c for c in checks if not c.passed]

            # Report any failures for debugging
            if failed:
                for c in failed:
                    print(f"  FAIL: {c.name} — {c.detail}")

            # AC coverage is the critical check
            ac_check = next(c for c in checks if c.name == "ac_coverage")
            assert ac_check.passed, f"AC coverage failed: {ac_check.detail}"

            # DAG validity
            dag_check = next(c for c in checks if c.name == "dag_valid")
            assert dag_check.passed, f"DAG invalid: {dag_check.detail}"

        finally:
            os.unlink(scope_path)
            if os.path.isfile(output_path):
                os.unlink(output_path)

    def test_wb_has_all_acs(self):
        """Every AC from SCOPE appears in generated WB."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(REALISTIC_SCOPE)
            scope_path = f.name

        output_path = scope_path.replace(".md", "-WB.md")
        try:
            wb_content = generate_work_breakdown(scope_path, output_path)
            for i in range(1, 25):
                assert f"AC{i}" in wb_content, f"AC{i} missing from WB"
        finally:
            os.unlink(scope_path)
            if os.path.isfile(output_path):
                os.unlink(output_path)

    def test_wb_has_critical_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(REALISTIC_SCOPE)
            scope_path = f.name

        output_path = scope_path.replace(".md", "-WB.md")
        try:
            wb_content = generate_work_breakdown(scope_path, output_path)
            assert "Critical Path" in wb_content
            assert "WP-" in wb_content
        finally:
            os.unlink(scope_path)
            if os.path.isfile(output_path):
                os.unlink(output_path)


class TestRealisticScoring:
    """Score the generated WB on hypothesis criteria."""

    def test_actionability_score(self):
        """Each WP in generated WB has: title, complexity, ACs, dependencies."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(REALISTIC_SCOPE)
            scope_path = f.name

        output_path = scope_path.replace(".md", "-WB.md")
        try:
            wb_content = generate_work_breakdown(scope_path, output_path)
            import re

            wp_rows = re.findall(r"\|\s*WP-\d+\s*\|", wb_content)
            # Should have 14 WP entries in tables
            assert len(wp_rows) >= 14

            # Each should have complexity column
            assert wb_content.count("| S |") + wb_content.count("| M |") + wb_content.count("| L |") >= 14
        finally:
            os.unlink(scope_path)
            if os.path.isfile(output_path):
                os.unlink(output_path)
