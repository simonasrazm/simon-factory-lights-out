"""SFLO pipeline constants — gates, grades, limits, state names."""

import os

# Root of the sflo repo — resolved from this file's location (src/ -> repo root)
SFLO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GATES = {
    1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
    2: {"artifact": "BUILD-STATUS.md", "role": "dev", "gate_doc": "gates/build.md"},
    3: {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
    4: {"artifact": "PM-VERIFY.md", "role": "pm", "gate_doc": "gates/verify.md"},
    5: {"artifact": "SHIP-DECISION.md", "role": "sflo", "gate_doc": "gates/ship.md"},
}

GRADE_MAP = {"A": 6, "A-": 5.5, "B+": 5, "B": 4, "B-": 3.5, "C": 3, "D": 2, "F": 1}
GRADE_THRESHOLD = 5  # B+

INNER_LOOP_MAX = 10
OUTER_LOOP_MAX = 10

KNOWN_ROLES = {"pm", "dev", "qa", "extra", "sflo-dir"}

S_INIT = "init"
S_SCOUT = "scout"
S_ASSIGN = "assign"
S_ESCALATE = "escalate"
S_DONE = "done"

PRE_GATE_PHASES = {
    S_SCOUT: {"artifact": "SCOUT-ASSIGNMENTS.md", "next_state": "gate-1"},
    S_ASSIGN: {"artifact": "SCOUT-ASSIGNMENTS.md", "next_state": "gate-1"},
}
