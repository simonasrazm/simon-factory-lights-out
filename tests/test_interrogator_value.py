"""R3-H4: Epic interrogator value-add.

This is a research test, not production behavior. It measures whether a
specific interrogator challenge can improve a structurally-valid but weak
WORK-BREAKDOWN.md enough to justify running the epic-interrogator on L/XL
scopes.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from validate_wb import validate_work_breakdown


SCOPE = """# SCOPE.md

## Acceptance Criteria
- [ ] AC1: Events are tenant-isolated by org.
- [ ] AC2: Viewers cannot access admin-only dashboard mutations.
- [ ] AC3: Dashboard widgets query live event data.
- [ ] AC4: Integration tests prove no cross-tenant leakage.
"""


BEFORE_WB = """# WORK-BREAKDOWN.md

## Meta
- Complexity: L
- Total WPs: 4
- Epic count: 2

## Work Packages

### WP-1: Event schema [Effort: S]
- **Type**: infrastructure
- **Inputs**: AC1
- **Outputs**: events table
- **Acceptance**: schema migration runs and stores events
- **Dependencies**: none

### WP-2: RBAC middleware [Effort: S]
- **Type**: cross-cutting
- **Inputs**: AC2
- **Outputs**: auth middleware
- **Acceptance**: viewer/admin checks pass
- **Dependencies**: none

### WP-3: Widget query API [Effort: M]
- **Type**: feature
- **Inputs**: AC3, WP-1, WP-2
- **Outputs**: GET /api/widgets/query
- **Acceptance**: widgets receive live data
- **Dependencies**: WP-1, WP-2

### WP-4: Leakage integration tests [Effort: M]
- **Type**: quality
- **Inputs**: AC4, WP-1, WP-2, WP-3
- **Outputs**: integration test suite
- **Acceptance**: cross-tenant leakage tests fail closed
- **Dependencies**: WP-3

## Epic Groups

### Epic 1: Foundation
**WPs**: WP-1, WP-2
**Context**: Establish persistence and authorization
**Integration Contract — PROVIDES:**
- Event table accepting JSON payloads
- RBAC middleware for protected routes
**Integration Contract — REQUIRES:**
- None

### Epic 2: Widgets and Verification
**WPs**: WP-3, WP-4
**Context**: Query widgets and prove isolation
**Integration Contract — PROVIDES:**
- GET /api/widgets/query endpoint with metric and time range params
- Integration test suite covering dashboard access
**Integration Contract — REQUIRES:**
- Epic 1: event rows include tenant_id and org_id fields
- Epic 1: RBAC middleware exposes role and tenant_id claims

## Dependency DAG
WP-1 -> WP-3 -> WP-4
WP-2 -> WP-3

## Critical Path
WP-1 -> WP-3 -> WP-4

## Cross-Cutting Concern Strategy
- Tenant isolation: resolved by query filters downstream.
- RBAC: middleware resolves access checks.

## AC Coverage
- AC1 -> WP-1
- AC2 -> WP-2
- AC3 -> WP-3
- AC4 -> WP-4
"""


AFTER_WB = BEFORE_WB.replace(
    "- Event table accepting JSON payloads\n- RBAC middleware for protected routes",
    "- Event table `events(id, tenant_id, org_id, type, payload, created_at)` accepting JSON payloads\n"
    "- RBAC middleware exporting `requireRole(role)` and request claims `{tenant_id, org_id, role}`",
).replace(
    "### WP-1: Event schema [Effort: S]\n"
    "- **Type**: infrastructure\n"
    "- **Inputs**: AC1\n"
    "- **Outputs**: events table\n"
    "- **Acceptance**: schema migration runs and stores events\n"
    "- **Dependencies**: none",
    "### WP-1: Event schema [Effort: S]\n"
    "- **Type**: infrastructure\n"
    "- **Inputs**: AC1\n"
    "- **Outputs**: events table with tenant_id and org_id columns\n"
    "- **Acceptance**: schema migration runs and rejects event rows without tenant_id/org_id\n"
    "- **Dependencies**: none\n"
    "- **Dev notes**: Add composite index `(tenant_id, org_id, created_at)` because WP-3 filters by both fields.\n"
    "- **Commit boundary**: migration, model, and tenant isolation regression test",
).replace(
    "### WP-2: RBAC middleware [Effort: S]\n"
    "- **Type**: cross-cutting\n"
    "- **Inputs**: AC2\n"
    "- **Outputs**: auth middleware\n"
    "- **Acceptance**: viewer/admin checks pass\n"
    "- **Dependencies**: none",
    "### WP-2: RBAC middleware [Effort: S]\n"
    "- **Type**: cross-cutting\n"
    "- **Inputs**: AC2\n"
    "- **Outputs**: auth middleware exposing tenant_id, org_id, and role claims\n"
    "- **Acceptance**: viewer/admin checks pass and missing tenant_id fails closed\n"
    "- **Dependencies**: none\n"
    "- **Dev notes**: Inject claims once at middleware boundary; downstream WPs must not parse JWT directly.\n"
    "- **Commit boundary**: middleware, claim parser, role tests",
)


def _upstream_contracts_satisfy_downstream_requires(wb_content: str) -> bool:
    epic_1 = re.search(
        r"### Epic 1:.*?(?=\n### Epic 2:)",
        wb_content,
        re.DOTALL,
    )
    epic_2 = re.search(
        r"### Epic 2:.*?(?=\n##|\Z)",
        wb_content,
        re.DOTALL,
    )
    assert epic_1 and epic_2

    provides = re.search(
        r"\*\*Integration Contract\s+[—-]\s+PROVIDES:\*\*\n(.*?)(?=\*\*Integration Contract\s+[—-]\s+REQUIRES:\*\*)",
        epic_1.group(0),
        re.DOTALL,
    )
    requires = re.search(
        r"\*\*Integration Contract\s+[—-]\s+REQUIRES:\*\*\n(.*?)(?=\n##|\Z)",
        epic_2.group(0),
        re.DOTALL,
    )
    assert provides and requires
    provided_text = provides.group(1)
    required_tokens = {"tenant_id", "org_id", "role"}
    return required_tokens.issubset(set(re.findall(r"[A-Za-z_]+", provided_text)))


def _readiness_score(wb_content: str) -> int:
    """45-point interrogator readiness score."""
    checks = {c.name: c.passed for c in validate_work_breakdown(wb_content, SCOPE)}

    dependencies = 10 if checks["dag_valid"] and checks["epic_ordering"] else 0
    contracts = 10 if checks["contract_specificity"] else 0
    if not _upstream_contracts_satisfy_downstream_requires(wb_content):
        contracts -= 6

    wp_count = len(re.findall(r"### WP-\d+:", wb_content))
    dev_notes = len(re.findall(r"\*\*Dev notes\*\*:", wb_content))
    commit_boundaries = len(re.findall(r"\*\*Commit boundary\*\*:", wb_content))
    feasibility = min(10, round(((dev_notes + commit_boundaries) / (2 * wp_count)) * 10))

    complexity = 10
    if "### WP-1: Event schema [Effort: S]" in wb_content and "tenant_id/org_id" not in wb_content:
        complexity -= 3

    cross_cutting = 5
    if "tenant_id" not in wb_content or "role" not in wb_content:
        cross_cutting -= 3

    return dependencies + contracts + feasibility + complexity + cross_cutting


def test_before_wb_is_structurally_valid_but_forward_incompatible():
    failed = [c for c in validate_work_breakdown(BEFORE_WB, SCOPE) if not c.passed]

    assert failed == []
    assert not _upstream_contracts_satisfy_downstream_requires(BEFORE_WB)


def test_interrogator_revision_improves_readiness_by_more_than_threshold():
    before = _readiness_score(BEFORE_WB)
    after = _readiness_score(AFTER_WB)

    assert before == 26
    assert after >= 40
    assert after - before >= 2


def test_after_wb_preserves_structural_validity_and_contract_compatibility():
    failed = [c for c in validate_work_breakdown(AFTER_WB, SCOPE) if not c.passed]

    assert failed == []
    assert _upstream_contracts_satisfy_downstream_requires(AFTER_WB)
