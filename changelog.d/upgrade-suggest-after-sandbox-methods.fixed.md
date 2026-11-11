**Migration tool (TypeScript): `--suggest-v2` after sandbox methods** —
`npx @daytona/upgrade --suggest-v2` no longer skips later `sandbox.fs` / `git`
/ `process` / … calls after a 0.x sandbox method such as `refreshData()` or
`setLabels()`.
