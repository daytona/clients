**CLI: default missing terminal geometry** — `sandbox exec --tty` uses
80×24 when the local terminal reports zero dimensions or cannot report its size.
