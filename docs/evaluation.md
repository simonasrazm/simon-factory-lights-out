# Evaluation evidence

SFLO's default model and skill choices are based on local comparative evaluation, not a claim that one configuration is universally best.

## Scope

The preserved evaluation corpus contains 788 substantive agent trials: 162 clean Developer trials, 81 trials across other roles, 324 linked QA and Security assessments, 129 TDD fine-tuning trials, 90 Terra/SOL optimization trials, and 2 manual comparison trials. Including accepted and discarded judge executions, smoke checks, and excluded holdouts, the work produced 951 successful model executions.

Trials covered simple, regular, and complex tasks in fresh isolated Git workspaces. User prompts stated functional requirements only; they did not prescribe tests or implementation process. Skill variants were compared against the same role without skills.

## Selected results

| Role and configuration | Professional passes | Quality | Mean time | Tokens | Mean cycles |
|---|---:|---:|---:|---:|---:|
| Developer, Terra medium, TDD + code-review | 12/12 | 96.965 | 100.254 s | 238,778 | 5.250 |
| Developer, SOL low, TDD + code-review | 12/12 | 97.269 | 148.001 s | 395,758 | 10.416 |
| QA, no skills | 8/9 | 91.25 | 170.0 s | 240,775 | — |
| QA, code-review | 9/9 | 93.67 | 168.5 s | 292,509 | — |
| QA, code-review + codebase-design | 9/9 | 91.33 | 221.2 s | 448,843 | — |

These figures are aggregate local evidence from the evaluated task set. Tokens are totals for each comparison cohort, while time and cycles are cohort means.

## Professional-quality methodology

Evaluation first applied professional admission criteria. Missing retained tests and tests that duplicated production logic were red cards. Poor testability, weak architecture, post-hoc testing, unnecessary complexity, and avoidable review rounds counted against a result. Admitted work was then assessed for functional correctness, meaningful coverage, tests-first behavior, mutation resistance, public-seam testing, refactor survival, architecture, YAGNI, and role-specific artifact quality. Cost and speed were compared only after professional quality.

Linked QA and Security assessments inspected Developer outputs so implementation quality was not judged only by the producing agent. Deterministic checks were kept separate from model-judge columns when judgment was required.

## End-to-end latency

End-to-end latency was not systematically benchmarked, so the table is not a factory-runtime service-level claim. A default factory can invoke up to six sequential roles, with validation and retries between them. Full runs can therefore take several minutes; a reported 13-minute two-file run is plausible even though the delivered code is small.

## Limits

This is a modest local task sample rather than an independent benchmark. Results can shift with model releases, task distribution, runtime conditions, and judge design. The raw corpus and methodology should be retained and expanded before making broader performance claims.
