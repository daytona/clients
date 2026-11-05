**Paused macOS sandboxes at a reservation end:** 30 minutes before a Mac
reservation that does not renew ends, each paused persistent macOS sandbox on it
stops. The sandbox keeps its disk but not its memory, and a later start can run
under another reservation. When the reservation of a paused sandbox is no longer
active, a resume answers `409 NO_ACTIVE_RESERVATION` with
`The Mac reservation holding this paused sandbox is ending. The sandbox stops at the end with its disk kept and can start again under another reservation.`
