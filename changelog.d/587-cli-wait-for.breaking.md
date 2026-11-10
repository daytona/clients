**CLI: `sandbox wait --for` checks its status** — `--for` takes `running`,
`stopped`, `paused` or `deleted`, or the SDK names `started` and `destroyed`;
any other value (a typo, a transient status, `RUNNING`) is a usage error
(exit 2) instead of a wait that runs to its timeout.
