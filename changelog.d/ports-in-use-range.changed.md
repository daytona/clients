**`ports.is_in_use()` / `ports.isInUse()` check the port first** — a port
outside 1–65535 (or not an integer) is refused with `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) before any request, as the other `ports` calls
already did. The sandbox still answers only for ports 3000–9999 and refuses any
other with `BAD_REQUEST`.
