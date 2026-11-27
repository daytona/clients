**Python: an async watch with plain callbacks lets other tasks run** — a
directory watch whose callbacks are plain (not coroutine) functions now yields
to the event loop between deliveries, so other tasks, including the code that
calls `stop()`, run while a backlog of events is delivered.
