# SFLO — Simon Factory Lights Out

**SFLO** is a gated pipeline protocol for building software products with AI coding agents. "Lights out" means you don't look at the code — you throw an idea in and test the outcome. It ensures nothing ships without proper discovery, development, testing, and verification — even when humans aren't watching.

Born from building software with AI agents that skip requirements, ship broken UIs, and declare "done" without evidence. SFLO makes that structurally impossible. Of course LLMs will bend rules. Of course this is not 100% reliable. Code wins over the LLM, but this system gives speed that is superior to precision (if that is what you need). I found that quality matters but only after the speed. Test many good enough things and then focus on quality. In addition this procedure has a cost aspect. Never seen in any popular agentic framework yet.

## The Problem

When you tell an AI agent "build me X":

- It skips requirements gathering and builds from vibes
- It does not test
- It declares "done" before anything actually works
- Context is lost between agent handoffs
- Quality bars are fake

## The Solution: 5 Gates

```
DISCOVER → BUILD → TEST → VERIFY → SHIP
```

Each gate produces a required artifact. No artifact = gate not passed. No skipping.

## Quick Start

1. Copy the protocol files to your AI agent workspace
2. When starting a new product: trigger `SFLO`
3. The orchestrating agent reads `sflo.md` and runs the pipeline
4. Each gate spawns a specialist agent with the right context
5. Ship only after all 5 gates pass

## Files

| File | Purpose |
|------|---------|
| `sflo.md` | The core pipeline — read this first |
| `gates/discovery.md` | Gate 1: PM Discovery — data sources, scope, acceptance criteria |
| `gates/build.md` | Gate 2: Developer Build — implementation with self-checks |
| `gates/test.md` | Gate 3: QA Testing — real data, real queries, graded |
| `gates/verify.md` | Gate 4: PM Verification — spec match confirmation |
| `gates/ship.md` | Gate 5: Ship Decision — evidence-based go/no-go |
| `roles.md` | Agent roles and selection heuristics |

## Design Principles

- **Evidence over assertion** — every gate produces a file with proof
- **Real data or fail** — mock/sample data is an automatic failure
- **Loops are explicit** — Dev↔QA cycles up to 10 rounds, then escalate
- **Context survives handoffs** — each agent reads predecessor's artifacts
- **Humans can override** — but overrides are logged

## License

[MIT](LICENSE)
