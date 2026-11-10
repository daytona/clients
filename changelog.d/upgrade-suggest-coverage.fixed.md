**Migration tool: `--suggest-v2` covers every call on a sandbox and its
services** — `npx @daytona/upgrade --suggest-v2` no longer skips later
`sandbox.fs` / `git` / `process` / … calls after a 0.x sandbox method such as
`refreshData()` or `setLabels()`; suggests `client.volume` / `snapshot` calls on
an unmigrated project as it does after the default pass (where it reported that
it could not prove the owning client, and after the pass gave nothing); and
renders an LSP server's `start()` on the sandbox that created it, or as a manual
move when that sandbox is unknown. `uvx daytona-upgrade` now traces the LSP
server's sandbox too, where it named a `sandbox` placeholder.
