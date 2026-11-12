**Largest disk of a persistent macOS sandbox** — a persistent macOS sandbox's
disk is at most 274432 MiB on `m4pro-12c-24g`, 233472 MiB on `m4pro-12c-48g` and
285696 MiB on `m5pro-15c-24g`; `m4-10c-16g` takes no persistent sandbox.
Persistent sandboxes at the default size still fit their Mac together. A request
that does not fit answers `409 NO_FREE_SLOT`, or `409 NO_MAC_AVAILABLE` when it
buys a reservation, and the message states the largest disk that fits.
