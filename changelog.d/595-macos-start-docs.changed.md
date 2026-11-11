**macOS start, pause and fork docs follow the service** — a start of a
stopped macOS sandbox buys a Mac reservation when the organization has none
in the region and auto-reserve is on, so it can fail as a create that buys
one does, `LimitExceededError` with `limit` `max_mac_reservations` included;
the start docs list every refusal. A paused sandbox keeps an auto-renewing
reservation renewing and is stopped, with its disk, before an end that does
not renew. A start, a fork and an image capture answer `DISK_NOT_READY`
while the disk is not ready yet. Only documentation and CLI help changed.
