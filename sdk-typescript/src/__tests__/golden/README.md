# Golden process contract suite

This suite pins the current live TypeScript SDK behavior for the legacy process, session, PTY, code-run, and interpreter APIs against the production daemon.

## Guarantees

- The assertions in these tests are the Phase-0 equivalence oracle for the legacy process surface.
- They document **current behavior**, including quirks and bugs that a refactor must preserve until intentionally changed.
- Stable values are asserted exactly. Volatile values are asserted by type/shape/regex only.

## Run only this suite

```bash
cd <repo-root>
export DAYTONA_API_KEY=<your-api-key> DAYTONA_API_URL=<api-url>
npx nx test:golden sdk-typescript
```

The target is gated on `DAYTONA_API_KEY` and runs only `src/__tests__/golden/**/*.test.ts`.

## Non-negotiable rule

Never relax an assertion to make a refactor pass.

If a test fails after a refactor, either:

1. the refactor changed real behavior and must be fixed, or
2. production behavior changed first and the golden contract must be deliberately re-probed and updated.
