"""sflo eval framework — base types and abstract base class.

Borrowed from three battle-tested patterns:
  LangChain Callbacks: typed ABC with multi-site lifecycle methods (on_llm_end-style verbs)
  Guardrails AI: YAML config + severity/category taxonomy
  Anthropic Claude Code Hooks: match selector pattern for fire-only-when-relevant

No security-specific logic lives here. Security plugins are provided by
host projects (declared in bindings.yaml `evals:` section).
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, List, Optional


class HookSite(str, Enum):
    PRE_PROMPT = "pre_prompt"
    POST_RESPONSE = "post_response"
    ON_RESPONSE_CHUNK = "on_response_chunk"  # streaming-aware (future use)
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    PRE_ARTIFACT = "pre_artifact"


class EvalSeverity(str, Enum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class EvalCategory(str, Enum):
    SECURITY = "security"
    COMPLIANCE = "compliance"
    QUALITY = "quality"
    CUSTOM = "custom"


class EvalAction(str, Enum):
    PASS = "pass"  # pipeline continues with original payload
    MODIFY = "modify"  # pipeline continues with eval-modified payload
    ABORT = "abort"  # raise EvalAbortError; pipeline halts


@dataclass
class EvalContext:
    role: str  # current agent role (scout/pm/dev/qa/sflo/interrogator)
    site: HookSite  # which lifecycle moment fired
    payload: dict  # site-specific (response_text, tool_args, etc)
    metadata: dict  # session_id, run_id, output_dir, gate_num, cwd, etc
    config: dict  # plugin's own config from bindings.yaml


@dataclass
class EvalResult:
    triggered: bool  # did the eval fire/match?
    severity: EvalSeverity
    category: EvalCategory
    payload: Optional[dict] = None  # modified payload if action=MODIFY
    action: EvalAction = EvalAction.PASS
    incident: Optional[dict] = (
        None  # for logging (timestamp, eval_name, evidence_hash, etc)
    )


class EvalAbortError(Exception):
    """Raised when an eval returns action=ABORT. Caught by adapter; surfaces to runner."""

    def __init__(self, eval_name: str, reason: str, incident: dict | None = None):
        self.eval_name = eval_name
        self.reason = reason
        self.incident = incident or {}
        super().__init__(f"{eval_name}: {reason}")


class SfloEval(ABC):
    """Base class for all sflo evals/guardrails.

    Subclass + override the hook-site methods you need. Default implementations
    PASS through so subclasses only touch the sites they care about.

    Lifecycle methods are async to match adapter; sync subclass methods work via
    asyncio.iscoroutinefunction check at call site (adapter handles both).

    Class attributes
    ----------------
    name     : unique identifier used in logs and incident records (required)
    sites    : list of HookSite values this plugin fires at (required)
    category : EvalCategory for classification (default: CUSTOM)
    priority : lower = runs first; ties broken by registration order (default: 100)
    """

    name: ClassVar[str] = ""
    sites: ClassVar[List[HookSite]] = []
    category: ClassVar[EvalCategory] = EvalCategory.CUSTOM
    priority: ClassVar[int] = 100

    def __init__(self, config: dict | None = None) -> None:
        self.config: dict = config or {}

    # ------------------------------------------------------------------
    # Default no-op implementations — subclass overrides only what it needs
    # ------------------------------------------------------------------

    async def pre_prompt(self, ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=False, severity=EvalSeverity.INFO, category=self.category
        )

    async def post_response(self, ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=False, severity=EvalSeverity.INFO, category=self.category
        )

    async def on_response_chunk(self, ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=False, severity=EvalSeverity.INFO, category=self.category
        )

    async def pre_tool_call(self, ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=False, severity=EvalSeverity.INFO, category=self.category
        )

    async def post_tool_call(self, ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=False, severity=EvalSeverity.INFO, category=self.category
        )

    async def pre_artifact(self, ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=False, severity=EvalSeverity.INFO, category=self.category
        )
