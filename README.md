# SFLO — Simon Factory Lights Out

A gated pipeline protocol for building software with AI agents. Five gates — each producing a required artifact. No artifact, no progress. No skipping.

```mermaid
flowchart LR
    G1["Gate 1<br/>DISCOVER<br/><br/>PM Agent<br/>SCOPE.md"] --> DEV_QA

    subgraph DEV_QA ["Inner Loop — max 10 rounds"]
        G2["Gate 2<br/>BUILD<br/><br/>Dev Agent<br/>BUILD-STATUS.md"] --> G3["Gate 3<br/>TEST<br/><br/>QA Agent<br/>QA-REPORT.md"]
        G3 -- "below threshold" --> G2
    end

    DEV_QA -- "meets threshold" --> G4["Gate 4<br/>VERIFY<br/><br/>PM Agent<br/>PM-VERIFY.md"]
    G4 -- "not A" --> DEV_QA
    G4 -- "A" --> G5["Gate 5<br/>SHIP<br/><br/>SFLO Agent<br/>SHIP-DECISION.md"]
```

## Install

If you want to use v1 (no code) - get it from [here](https://github.com/simonasrazm/simon-factory-lights-out/commit/7c53dba87045d3ae80b4b01bb23d4cbf09941b84)

### Latest version install

Tell your AI agent:

> Install SFLO from https://github.com/simonasrazm/simon-factory-lights-out

The agent will clone the repo, run `setup.sh`, install the pipeline hook, and configure bindings. After a gateway restart (OpenClaw) or new session (Claude Code), SFLO is ready.

## Usage

Say **"SFLO: [describe what to build]"** to start the pipeline. Examples:

- "SFLO: build a job board website with search and filters"
- "SFLO: create a CLI tool that scans code for vulnerabilities"

The pipeline runs automatically — Scout picks the right agents, gates enforce quality, hooks keep it moving until done or escalated.

## Agents

Gates define **what** to produce. Agents define **how**. Each agent is a directory with a `SOUL.md` (methodology) and a `BRIEF.md` (one-paragraph description for Scout matching). See `docs/agent-spec.md` for the spec.

### How Scout picks agents

On user prompt, Scout scans `agents/` directory and reads each `BRIEF.md` to understand what the agent specializes in. It then matches agents to pipeline roles based on the user's prompt. Scout is an LLM agent.

```mermaid
flowchart TD
    P["User prompt:<br/>'Build a weather dashboard'"] --> S["Scout reads prompt"]
    S --> SCAN["Scans agent directories"]
    SCAN --> B1["agents/pm/BRIEF.md<br/>'Generic PM for any project'"]
    SCAN --> B2["agents/pm-website/BRIEF.md<br/>'PM specialized for web apps.<br/>Web-specific acceptance criteria.'"]
    SCAN --> B3["agents/pm-mobile/BRIEF.md<br/>'PM for mobile apps. Platform-specific<br/>criteria for iOS and Android.'"]
    B1 --> MATCH{"Match prompt<br/>to role"}
    B2 --> MATCH
    B3 --> MATCH
    MATCH -- "web app → pm-website" --> A["PM: agents/pm-website"]
    MATCH -- "no match → generic" --> G["PM: agents/pm"]
```

**Example:** When the prompt says "build a weather dashboard," Scout reads all BRIEF.md files, sees that `pm-website` specializes in web apps, and assigns it as PM. If no better agent matches, Scout falls back to the generic agent (`agents/pm`).

### Adding your own agents

Create a directory with two files:

```
agents/
  my-pm-agent/
    BRIEF.md      ← one paragraph, tells Scout when to use this agent
    SOUL.md       ← full methodology, read by the agent at runtime
```

Scout will discover your agent automatically on the next pipeline run — no configuration needed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
