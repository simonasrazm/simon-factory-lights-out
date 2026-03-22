# Agent Spec

An **agent** in SFLO is a directory that tells an AI model how to perform a specific role in the pipeline. The directory must contain one file: `SOUL.md`.

## Why Agents?

The SFLO gates (in `gates/`) define **what** each role must produce. Agents define **how**. This separation means:

- You can swap agents without changing the pipeline
- Different teams can use different agents for the same role
- If no agent is assigned, the gate definition itself serves as the instruction — agents are an upgrade, not a requirement

## Required: SOUL.md

Every agent directory must have a `SOUL.md` file. This is the file an AI model reads to understand its job. It must cover four things:

1. **Who you are** — the agent's role and perspective
2. **How you work** — methodology, principles, decision-making approach
3. **What to read first** — which files to consume before starting (gate docs, predecessor artifacts, reference material)
4. **What to produce** — the output artifact(s) and their format

That's it. No specific format is enforced. Write it however makes the agent most effective.

## Recommended: BRIEF.md

A one-paragraph file describing when to use this agent and what it specializes in. The Agent Scout reads `BRIEF.md` for quick matching during startup. If missing, Scout falls back to reading `SOUL.md`.

Keep it short — one paragraph, plain language. Answer: "Use this agent when [project type/condition]."
