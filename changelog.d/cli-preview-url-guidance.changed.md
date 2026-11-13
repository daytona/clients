**CLI: preview URL guidance** — `sandbox port preview-url` notes on stderr that
a private port's token goes in the `X-Daytona-Preview-Token` header and names
the two browser options, `--signed` (short-lived) and `expose --public`; a
refused `--signed` link points to `expose --public` in its hint, and `sandbox
create --port` says it is a platform port mapping, not a preview URL.
