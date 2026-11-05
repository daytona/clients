---
name: coverage-preflight
description: >
  Measures patch coverage of the current branch — the share of added, coverable
  lines the test suites execute — against the 60% target, and adds tests until
  it is met. Use before opening a PR, or when the user says "check coverage",
  "patch coverage", or "is this tested enough?".
---

# Coverage preflight

New code should arrive with tests that run it. This skill measures that for the
lines a branch adds, with `scripts/patch_coverage.py`, and closes the gap.

## Steps

1. **Base.** The branch's merge base with the 2.x line, `origin/main` (the
   script's default), or
   `--base <ref>` for another target branch (for example `release/v0`).

2. **Reports for every component the diff touches** (from the repository root):

   ```bash
   (cd sdk-python && .venv/bin/python -m pytest -q --cov=daytona --cov-report=xml)
   (cd sdk-typescript && npx jest --coverage --coverageReporters=lcov)
   (cd cli && go test ./... -coverprofile=coverage.out)
   ```

   The CI workflows produce the same reports and upload them as artifacts
   (`coverage-python`, `coverage-typescript`, `coverage-cli`).

3. **Measure:** `python3 scripts/patch_coverage.py`. It prints the ratio, the
   verdict, and every file's uncovered added lines. Untracked files are
   invisible to `git diff`: `git add -N` them first.

4. **Close the gap (at most five rounds).** For each uncovered line, add a test
   that exercises the behavior — through the public API where the line is
   reachable from it — in both SDKs when the change is SDK behavior (parity).
   Re-run step 2 for the touched component and step 3. Stop when the target is
   met.

5. **Untestable residue.** Code that only runs against a live deployment (it is
   covered by `e2e/run.sh`, not by unit tests) may stay uncovered. Say so in the
   PR description with the lines and the reason. Never add a test that only
   executes a line without asserting its behavior.

## Report

```
## Coverage preflight
Base: <sha>
Patch coverage: <covered>/<coverable> = <pct>% (target 60%) → PASS | FAIL
Tests added: <files>
Uncovered residue: <file:lines — reason> (or "none")
```
