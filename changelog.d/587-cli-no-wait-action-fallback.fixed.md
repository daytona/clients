**CLI: accepted `--no-wait` actions stay successful** — when `start`, `stop`,
`pause`, `resume`, `fork` or `delete` was accepted but reading the sandbox
afterwards fails, the command prints the API's acknowledgement, warns on
stderr and exits 0, so a retry does not repeat the action (a second fork).
