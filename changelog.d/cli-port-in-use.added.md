**CLI: `sandbox port in-use`** — reports whether a port is in use inside a
sandbox. A port outside 1-65535 is a usage error before any request; a port
inside it but outside the range the sandbox checks (currently 3000 to 9999)
reaches the sandbox, which answers `BAD_REQUEST`.
