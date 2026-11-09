**Waits no longer stall until the reconcile timer when a state event is
missed** — with the events hub on, `create()`, `start()` and `wait()` could
end only at the ~60 s reconcile when the socket delivered no event for the
transition, and a `create()` whose wait budget ran out first returned a
sandbox that had already started as still starting. The hub now
re-subscribes, in one batched frame, every id a wait is watching that heard
no state frame for 5–10 s, so a missed event delays a wait by at most that
long. No extra HTTP requests are made.
