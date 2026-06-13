"""R3-H5: Gate 1.5 Validation Completeness — Empirical Test.

Tests validate_wb.py against intentionally broken WBs to measure:
- Detection rate on structural defects
- False positive rate on valid WBs
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from validate_wb import (
    validate_work_breakdown,
    check_ac_coverage,
    check_dag_validity,
    check_epic_ordering,
    check_contract_specificity,
    check_wp_completeness,
    check_no_orphan_wps,
)


# ---------------------------------------------------------------------------
# VALID WB — should produce zero failures
# ---------------------------------------------------------------------------

VALID_WB = """# WORK-BREAKDOWN.md

## Meta
- Total WPs: 5
- Epic count: 2

## Work Packages

### WP-1: DB Schema [Effort: M]
- **Type**: infrastructure
- **Inputs**: SCOPE AC1, AC2
- **Outputs**: migrations/, models.py
- **Acceptance**: migration runs clean, tenant isolation verified
- **Dependencies**: none

### WP-2: Event Pipeline [Effort: M]
- **Type**: feature
- **Inputs**: SCOPE AC3, AC4
- **Outputs**: ingestion service
- **Acceptance**: events ingested at 1k/s benchmark
- **Dependencies**: WP-1

### WP-3: RBAC Middleware [Effort: S]
- **Type**: cross-cutting
- **Inputs**: SCOPE AC5, AC6
- **Outputs**: auth middleware
- **Acceptance**: role-based access control verified on all routes
- **Dependencies**: none

### WP-4: Query Engine [Effort: M]
- **Type**: feature
- **Inputs**: SCOPE AC7, AC8
- **Outputs**: query API endpoint
- **Acceptance**: aggregation returns correct results
- **Dependencies**: WP-1, WP-3

### WP-5: Dashboard UI [Effort: M]
- **Type**: feature
- **Inputs**: SCOPE AC9, AC10
- **Outputs**: dashboard SPA
- **Acceptance**: renders with live data from query engine
- **Dependencies**: WP-4

## Epic Groups

### Epic 1: Foundation
**WPs**: WP-1, WP-2, WP-3
**Integration Contract — PROVIDES:**
- Tenant-isolated PostgreSQL schema with RLS policies
- Event ingestion queue accepting JSON payloads at POST /events
- RBAC middleware exporting `requireRole(role)` Express middleware

### Epic 2: Features
**WPs**: WP-4, WP-5
**Integration Contract — PROVIDES:**
- Query API at GET /api/query with aggregation params
- Dashboard SPA at /dashboard rendering live widgets
**Integration Contract — REQUIRES:**
- Epic 1: DB schema, RBAC middleware
"""

VALID_SCOPE = """## Acceptance Criteria
- [ ] AC1: Tenant isolation verified
- [ ] AC2: Schema migrations reversible
- [ ] AC3: Events ingested at 1k/s
- [ ] AC4: Event validation rejects malformed
- [ ] AC5: Admin role has full access
- [ ] AC6: Viewer role is read-only
- [ ] AC7: Aggregation query returns correct sums
- [ ] AC8: Query handles 100k rows in <2s
- [ ] AC9: Dashboard renders 5 widgets
- [ ] AC10: Real-time updates via WebSocket
"""


# ---------------------------------------------------------------------------
# BROKEN WBs — each targets one specific defect
# ---------------------------------------------------------------------------

# Defect 1: Missing AC coverage (AC7 not in any WP)
BROKEN_MISSING_AC = VALID_WB.replace("AC7, AC8", "AC8")

# Defect 2: Circular dependency (WP-3 depends on WP-4, WP-4 depends on WP-3)
BROKEN_CYCLE = VALID_WB.replace(
    "### WP-3: RBAC Middleware [Effort: S]\n- **Type**: cross-cutting\n- **Inputs**: SCOPE AC5, AC6\n- **Outputs**: auth middleware\n- **Acceptance**: role-based access control verified on all routes\n- **Dependencies**: none",
    "### WP-3: RBAC Middleware [Effort: S]\n- **Type**: cross-cutting\n- **Inputs**: SCOPE AC5, AC6\n- **Outputs**: auth middleware\n- **Acceptance**: role-based access control verified on all routes\n- **Dependencies**: WP-4",
)

# Defect 3: Epic ordering violation (WP in Epic 1 depends on WP in Epic 2)
BROKEN_EPIC_ORDER = VALID_WB.replace(
    "### WP-2: Event Pipeline [Effort: M]\n- **Type**: feature\n- **Inputs**: SCOPE AC3, AC4\n- **Outputs**: ingestion service\n- **Acceptance**: events ingested at 1k/s benchmark\n- **Dependencies**: WP-1",
    "### WP-2: Event Pipeline [Effort: M]\n- **Type**: feature\n- **Inputs**: SCOPE AC3, AC4\n- **Outputs**: ingestion service\n- **Acceptance**: events ingested at 1k/s benchmark\n- **Dependencies**: WP-5",
)

# Defect 4: Vague contract ("provides an API" instead of concrete shape)
BROKEN_VAGUE_CONTRACT = VALID_WB.replace(
    "- Query API at GET /api/query with aggregation params",
    "- Provides an API",
)

# Defect 5: WP missing acceptance criteria
BROKEN_NO_ACCEPTANCE = VALID_WB.replace(
    "### WP-3: RBAC Middleware [Effort: S]\n- **Type**: cross-cutting\n- **Inputs**: SCOPE AC5, AC6\n- **Outputs**: auth middleware\n- **Acceptance**: role-based access control verified on all routes\n- **Dependencies**: none",
    "### WP-3: RBAC Middleware [Effort: S]\n- **Type**: cross-cutting\n- **Inputs**: SCOPE AC5, AC6\n- **Outputs**: auth middleware\n- **Dependencies**: none",
)

# Defect 6: Orphan WP (in epic but no section)
BROKEN_ORPHAN = VALID_WB.replace(
    "**WPs**: WP-4, WP-5",
    "**WPs**: WP-4, WP-5, WP-6",
)

# Defect 7: WP defined but not in any epic
BROKEN_UNASSIGNED = VALID_WB + """
### WP-6: Orphan Feature [Effort: S]
- **Type**: feature
- **Inputs**: SCOPE AC10
- **Outputs**: orphan.py
- **Acceptance**: exists
- **Dependencies**: none
"""


# ---------------------------------------------------------------------------
# TESTS: Valid WB produces zero failures (false positive rate = 0%)
# ---------------------------------------------------------------------------


class TestValidWB:
    def test_all_checks_pass(self):
        checks = validate_work_breakdown(VALID_WB, VALID_SCOPE)
        failed = [c for c in checks if not c.passed]
        assert failed == [], f"False positives: {[(c.name, c.detail) for c in failed]}"

    def test_ac_coverage_passes(self):
        result = check_ac_coverage(VALID_WB, VALID_SCOPE)
        assert result.passed

    def test_dag_valid(self):
        result = check_dag_validity(VALID_WB)
        assert result.passed

    def test_epic_ordering_valid(self):
        result = check_epic_ordering(VALID_WB)
        assert result.passed

    def test_contracts_specific(self):
        result = check_contract_specificity(VALID_WB)
        assert result.passed

    def test_wps_complete(self):
        result = check_wp_completeness(VALID_WB)
        assert result.passed

    def test_no_orphans(self):
        result = check_no_orphan_wps(VALID_WB)
        assert result.passed


# ---------------------------------------------------------------------------
# TESTS: Broken WBs detected (detection rate)
# ---------------------------------------------------------------------------


class TestBrokenDetection:
    """Each test verifies a specific defect type is caught."""

    def test_detects_missing_ac(self):
        result = check_ac_coverage(BROKEN_MISSING_AC, VALID_SCOPE)
        assert not result.passed
        assert "AC7" in result.detail

    def test_detects_cycle(self):
        result = check_dag_validity(BROKEN_CYCLE)
        assert not result.passed
        assert "Circular" in result.detail or "cycle" in result.detail.lower()

    def test_detects_epic_ordering_violation(self):
        result = check_epic_ordering(BROKEN_EPIC_ORDER)
        assert not result.passed
        assert "WP-2" in result.detail
        assert "Epic" in result.detail

    def test_detects_vague_contract(self):
        result = check_contract_specificity(BROKEN_VAGUE_CONTRACT)
        assert not result.passed
        assert "Vague" in result.detail or "vague" in result.detail.lower()

    def test_detects_missing_acceptance(self):
        result = check_wp_completeness(BROKEN_NO_ACCEPTANCE)
        assert not result.passed
        assert "WP-3" in result.detail

    def test_detects_orphan_wp(self):
        result = check_no_orphan_wps(BROKEN_ORPHAN)
        assert not result.passed
        assert "WP-6" in result.detail

    def test_detects_unassigned_wp(self):
        result = check_no_orphan_wps(BROKEN_UNASSIGNED)
        assert not result.passed
        assert "WP-6" in result.detail


# ---------------------------------------------------------------------------
# TESTS: Combined detection — full validate_work_breakdown on each broken WB
# ---------------------------------------------------------------------------


class TestFullValidation:
    """Verify full validation pipeline catches all defects."""

    def test_missing_ac_caught_in_full(self):
        checks = validate_work_breakdown(BROKEN_MISSING_AC, VALID_SCOPE)
        failed_names = [c.name for c in checks if not c.passed]
        assert "ac_coverage" in failed_names

    def test_cycle_caught_in_full(self):
        checks = validate_work_breakdown(BROKEN_CYCLE, VALID_SCOPE)
        failed_names = [c.name for c in checks if not c.passed]
        assert "dag_valid" in failed_names

    def test_epic_order_caught_in_full(self):
        checks = validate_work_breakdown(BROKEN_EPIC_ORDER, VALID_SCOPE)
        failed_names = [c.name for c in checks if not c.passed]
        assert "epic_ordering" in failed_names

    def test_vague_caught_in_full(self):
        checks = validate_work_breakdown(BROKEN_VAGUE_CONTRACT, VALID_SCOPE)
        failed_names = [c.name for c in checks if not c.passed]
        assert "contract_specificity" in failed_names

    def test_no_acceptance_caught_in_full(self):
        checks = validate_work_breakdown(BROKEN_NO_ACCEPTANCE, VALID_SCOPE)
        failed_names = [c.name for c in checks if not c.passed]
        assert "wp_completeness" in failed_names

    def test_orphan_caught_in_full(self):
        checks = validate_work_breakdown(BROKEN_ORPHAN, VALID_SCOPE)
        failed_names = [c.name for c in checks if not c.passed]
        assert "no_orphan_wps" in failed_names


# ---------------------------------------------------------------------------
# SUMMARY: Scoring for hypothesis results
# ---------------------------------------------------------------------------


class TestHypothesisScoring:
    """Meta-test: compute detection rate and false positive rate."""

    def test_detection_rate(self):
        """All 7 defect types must be caught."""
        defect_tests = [
            ("missing_ac", BROKEN_MISSING_AC, "ac_coverage"),
            ("cycle", BROKEN_CYCLE, "dag_valid"),
            ("epic_order", BROKEN_EPIC_ORDER, "epic_ordering"),
            ("vague_contract", BROKEN_VAGUE_CONTRACT, "contract_specificity"),
            ("no_acceptance", BROKEN_NO_ACCEPTANCE, "wp_completeness"),
            ("orphan_wp", BROKEN_ORPHAN, "no_orphan_wps"),
            ("unassigned_wp", BROKEN_UNASSIGNED, "no_orphan_wps"),
        ]

        detected = 0
        for defect_name, wb, expected_check in defect_tests:
            checks = validate_work_breakdown(wb, VALID_SCOPE)
            failed_names = [c.name for c in checks if not c.passed]
            if expected_check in failed_names:
                detected += 1

        rate = detected / len(defect_tests) * 100
        assert rate == 100.0, f"Detection rate: {rate}% ({detected}/{len(defect_tests)})"

    def test_false_positive_rate(self):
        """Valid WB must pass ALL checks (0% false positive)."""
        checks = validate_work_breakdown(VALID_WB, VALID_SCOPE)
        false_positives = [c for c in checks if not c.passed]
        rate = len(false_positives) / len(checks) * 100
        assert rate == 0.0, f"False positive rate: {rate}% — {[(c.name, c.detail) for c in false_positives]}"
