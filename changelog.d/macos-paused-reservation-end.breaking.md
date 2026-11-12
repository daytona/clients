**Paused macOS sandboxes at a reservation end:** 30 minutes before a Mac
reservation that does not renew ends, each paused persistent macOS sandbox on it
stops. On a new Mac reservation, the sandbox keeps its disk, loses its memory,
and can start again under another reservation. On an earlier Mac reservation,
the reservation end saves the sandbox as an image and then deletes the sandbox.
A resume of a sandbox on a new Mac reservation that is ending answers
`409 NO_ACTIVE_RESERVATION` with
`The Mac reservation holding this paused sandbox is ending. The sandbox stops at the end with its disk kept and can start again under another reservation.`
