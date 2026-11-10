**CLI: `--project` must match a project API key** — with a project API key,
a `--project` / `DAYTONA_PROJECT` that names another project was accepted and
ignored, so a command acted in the key's project; it is now a usage error
(exit 2) on every command that acts in a project and on `whoami`.
