**CLI: `sandbox cp` checks its local source first** — before anything is signed
or sent, `sandbox cp` makes a missing local source, a local directory without
`-r`, and a local file with `-r` usage errors (exit 2), instead of the failure
they hit later.
