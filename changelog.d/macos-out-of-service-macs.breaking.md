**macOS sandboxes avoid Macs out of service** — a create or a start no longer
goes to a Mac that the platform has taken out of service. A start of a sandbox
that last ran on such a Mac can answer `409 DISK_NOT_READY` for a time; retry
it, and it runs on another Mac of the organization. A create or start that cannot
find another in-service Mac with space answers `409 NO_FREE_SLOT` at once; so
does a create explicitly targeting a reservation on an out-of-service Mac.
Before, the request waited for that Mac and could time out.
