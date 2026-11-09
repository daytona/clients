**Python build-log streams free their connection when a release stalls** — a
build-log read or follower whose stream release runs out its grace window, or
whose client fails to close, now closes its own client in a window of its own,
and hard when that fails, before the stream counts as released, so its
connection is freed instead of held.
