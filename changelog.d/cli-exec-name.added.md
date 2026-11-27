**CLI: `sandbox exec --name`** — gives the process a live name that other
process commands accept in place of its id. A name that is not 1-128
characters of `[A-Za-z0-9._-]` (or is `.` / `..`) is a usage error before any
request.
