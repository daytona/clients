**Migration tool: filesystem suggestions say v2 paths are absolute** — every
`sandbox.fs` suggestion of `npx @daytona/upgrade --suggest-v2` and `uvx
daytona-upgrade --suggest-v2`, and the migration guide, now note in the same
words that v2 takes only absolute sandbox paths and refuses a relative one,
which 0.x resolved against the sandbox working directory.
