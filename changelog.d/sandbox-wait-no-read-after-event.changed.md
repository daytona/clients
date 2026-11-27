**One request fewer per lifecycle wait** — when the sandbox events stream
reports the state a wait is waiting for, `wait()`, `start()`, `stop()`,
`pause()`, `resume()` and the create and fork waits now return without
reading the sandbox again, as `delete()` already did (Python, TypeScript and
Go). The handle takes the event's state and the sandbox's error message,
which an event without one now clears, and keeps its other fields,
`spot_evicted_at` / `spotEvictedAt` / `SpotEvictedAt`, the lifecycle-policy
deadlines and the activity clock included, as of the last read: call
`refresh_data()` / `refreshData()` / `Refresh` to reload them. A terminal
state the stream reports still gets one confirming read, and a wait the
stream leaves undecided still reads the sandbox while it polls. `start()` and
`resume()` of a macOS sandbox still read it once more after the event, while
budget remains, for the boot mode and the reservation. A terminal state the
events stream reports now carries the stream's error message as the raised
error's `reason` in Python and TypeScript, as in Go.
