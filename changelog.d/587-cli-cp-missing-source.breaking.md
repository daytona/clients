**CLI: `sandbox cp` checks its local source first** — a missing local source of
`sandbox cp` is a usage error (exit 2) before anything is signed or sent.
