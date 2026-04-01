"""SFLO prompt generation — translates state machine output to reinjectable instructions."""

from .constants import PYTHON_CMD


def format_prompt(action_dict):
    """Translate a compute_next() result into a reinjectable instruction string."""
    action = action_dict.get("action", "")

    if action in ("pipeline_complete", "waiting", "ask_human"):
        return None

    if action == "spawn_agent":
        agent = action_dict.get("agent", {})
        role = agent.get("role", "unknown")
        model = agent.get("model", "sonnet")
        path = agent.get("path", "")
        reads = agent.get("reads", [])
        produces = agent.get("produces", "")
        instruction = agent.get("instruction", "")

        reads_str = "\n".join(f"  - {r}" for r in reads)
        lines = [
            "SFLO PIPELINE — next action required.",
            "",
            f"Spawn the {role.upper()} agent with the Agent tool:",
            f"  model: {model}",
            f"  path: {path}",
        ]
        if reads:
            lines.append("  reads:")
            lines.append(reads_str)
        if produces:
            lines.append(f"  produces: {produces}")
        if instruction:
            lines.append(f"  instruction: {instruction}")
        lines.append("")
        lines.append("Tell the agent to read the files listed above and produce the artifact.")
        lines.append("Do NOT paraphrase gate docs — tell the agent to read them directly.")
        lines.append(f"After the agent finishes, run: {PYTHON_CMD} sflo/src/scaffold.py next")
        return "\n".join(lines)

    if action == "produce_artifact":
        artifact = action_dict.get("artifact", "")
        reads = action_dict.get("reads", [])
        gate_doc = action_dict.get("gate_doc", "")
        reads_str = "\n".join(f"  - {r}" for r in reads)
        lines = [
            f"SFLO PIPELINE — produce artifact: {artifact}",
            "",
            f"Read the gate doc: {gate_doc}",
            "Read these prior artifacts:",
            reads_str,
            "",
            f"Produce the artifact in .sflo/{artifact} following the gate doc template.",
            f"After writing the artifact, run: {PYTHON_CMD} sflo/src/scaffold.py next",
        ]
        return "\n".join(lines)

    if action == "validated":
        gate = action_dict.get("gate", "?")
        next_action = action_dict.get("next", {})
        next_prompt = format_prompt(next_action)
        if next_prompt:
            return f"Gate {gate} passed validation.\n\n{next_prompt}"
        return None

    if action == "loop_back":
        gate = action_dict.get("gate", "?")
        checks = action_dict.get("checks", [])
        inner = action_dict.get("inner_count", 0)
        outer = action_dict.get("outer_count", 0)
        max_loops = action_dict.get("max", 10)
        next_action = action_dict.get("next", {})
        next_prompt = format_prompt(next_action)

        failed_checks = [c for c in checks if not c.get("pass")]
        fails_str = ", ".join(c.get("name", "?") for c in failed_checks)

        lines = [
            f"SFLO PIPELINE — Gate {gate} FAILED. Looping back.",
            f"Failed checks: {fails_str}",
            f"Loop iteration: {inner or outer}/{max_loops}",
        ]
        if next_prompt:
            lines.append("")
            lines.append(next_prompt)
        return "\n".join(lines)

    return f"SFLO PIPELINE — continue. Run: {PYTHON_CMD} sflo/src/scaffold.py next"
