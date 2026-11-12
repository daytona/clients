**macOS creates and starts that stall on a Mac:** if a macOS create or start
makes no progress on its Mac for 900 seconds, the platform ends it. A start goes
back to `stopped` and keeps its disk. A create is deleted, as at the end of its
`create_timeout_seconds`.
