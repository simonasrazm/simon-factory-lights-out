"""SFLO pipeline constants — grades, limits, state names, and config-loaded gates."""

import os

# Root of the sflo repo — resolved from this file's location (src/ -> repo root)
SFLO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load pipeline config (gates, threshold, guardian) from pipeline.yaml
# Resolution: cwd/pipeline.yaml -> sflo/pipeline.yaml -> built-in defaults
from .config import load_pipeline_config

_config = load_pipeline_config()

GATES = _config["gates"]
GRADE_THRESHOLD = _config["grade_threshold"]
GUARDIAN_CONFIG = _config["guardian"]

GRADE_MAP = {"A": 6, "A-": 5.5, "B+": 5, "B": 4, "B-": 3.5, "C": 3, "D": 2, "F": 1}

INNER_LOOP_MAX = 10
OUTER_LOOP_MAX = 10

KNOWN_ROLES = {"pm", "dev", "qa", "extra", "sflo-dir"}

S_INIT = "init"
S_SCOUT = "scout"
S_ASSIGN = "assign"
S_ESCALATE = "escalate"
S_DONE = "done"
