# Gate 2: Developer Build

**Agent:** Developer
**Input:** SCOPE.md from Gate 1
**Produces:** Working app + `BUILD-STATUS.md`

## BUILD-STATUS.md Template

```markdown
## Build Status

- Build: SUCCESS (zero errors)
- Data loading: [N] records from [source]
- Core features: All ACs addressed

## Self-Check

- [ ] Real data loads (not mock/sample)
- [ ] Core use case works end-to-end
- [ ] Error states handled gracefully
- [ ] Each acceptance criterion from SCOPE.md addressed
- [ ] Every file declared under SCOPE.md `## Deliverables` exists at that exact project-relative path
- [ ] [Add project-specific checks based on SCOPE.md]
```

## Gate Check

- [ ] Build produces zero errors
- [ ] BUILD-STATUS.md exists with all checks marked
- [ ] App actually starts and shows real data
- [ ] Declared user deliverables exist as files under the project root, not under `.sflo/`

The scaffold validates these checks before advancing to Gate 3.
