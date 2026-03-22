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
- [ ] [Add project-specific checks based on SCOPE.md]
```

## Gate Check

- [ ] Build produces zero errors
- [ ] BUILD-STATUS.md exists with all checks marked
- [ ] App actually starts and shows real data

The scaffold validates these checks before advancing to Gate 3.
