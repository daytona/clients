**Python: a synchronous watch's `stop()` no longer waits for the event backlog**
— with the synchronous client, `WatchHandle.stop()` called from another thread
returns once the callback in flight (and `on_exit`) finish, as documented,
instead of after every queued event was delivered: no event is delivered after
the one in flight. Other calls on the client (such as `exit_error`) also run
between the deliveries of a backlog, and an async watch with plain callbacks
lets other tasks run between them.
