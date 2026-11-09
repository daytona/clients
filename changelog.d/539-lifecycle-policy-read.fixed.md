**One request for a Linux lifecycle-policy update** — Python
`update_lifecycle_policy()` no longer reads the sandbox before the `PATCH` on
a Linux handle whose class is known; it reads it only when the class is
unknown, or for a macOS sandbox whose persistence is unknown, as TypeScript
does.
