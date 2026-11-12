**macOS sandboxes on an earlier Mac reservation:** a Mac reservation that
started before this release keeps the earlier version of macOS sandboxes until
it ends. On that reservation, fork, image capture, and a create from an image
are not available. The API answers `409 CONFLICT` with a text that names the
cause and points to a new Mac reservation. Stop, start, pause, and resume of its
sandboxes operate as before, on the same Mac. At the end of that reservation,
the platform saves each persistent sandbox as an image, as before, and then
deletes the sandbox. A start, or a create from an image, that names no
reservation uses only a new Mac reservation.
