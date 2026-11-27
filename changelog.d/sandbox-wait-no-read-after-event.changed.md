**One request fewer per lifecycle wait** — when the sandbox events stream
reports the state a wait is waiting for, `wait()`, `start()`, `stop()`,
`pause()`, `resume()` and the create and fork waits now return without
reading the sandbox again, as `delete()` already did (Python, TypeScript and
Go). A terminal event (`error`, `destroying`) still gets one confirming read
before the wait raises, except after a `destroyed` report or once the budget
is spent; when that read is skipped or fails, the handle carries the reported
state. A wait the stream leaves undecided still reads the sandbox while it
polls. The handle takes an event's state and error message (an event without
one clears the handle's), and keeps
its other fields, `spot_evicted_at` / `spotEvictedAt` / `SpotEvictedAt`, the
lifecycle-policy deadlines and the activity clock included, as of the last
read: call `refresh_data()` / `refreshData()` / `Refresh` to reload them.
`start()` and `resume()` of a macOS sandbox still read it once more after the
event, while budget remains, for the boot mode and the reservation; a failed
read there does not raise and keeps the observed success. A
terminal event now carries the stream's error message as the raised error's
`reason` in Python and TypeScript, as in Go.
