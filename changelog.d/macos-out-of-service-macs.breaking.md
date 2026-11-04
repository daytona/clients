**macOS sandboxes avoid Macs taken out of service:** a create or a start no
longer goes to a Mac that the platform has taken out of service. A start of a
sandbox whose newest changes are on such a Mac answers `409 DISK_UPLOAD_PENDING`
until that Mac lets the disk go, and then runs on another Mac of the
organization. A create, or a start with no other Mac that has room, answers
`409 NO_FREE_SLOT` at once, and so does a create whose `reservation` names a
reservation on such a Mac. Before, the request waited for that Mac and could
time out.
