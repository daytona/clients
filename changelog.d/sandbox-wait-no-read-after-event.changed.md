**One request fewer per lifecycle wait** — when the sandbox events stream
reports the state a wait is waiting for, `wait()`, `start()`, `stop()`,
`pause()`, `resume()`, `delete()` and the create and fork waits now return
without reading the sandbox again (Python, TypeScript and Go). The handle
takes the reported state and the sandbox's error message, which a report
without one now clears, and keeps its
other fields, lifecycle-policy deadlines included, as of the last read: call
`refresh_data()` / `refreshData()` / `Refresh` to reload them. `start()` and
`resume()` of a macOS sandbox still read it once more, for the boot mode and
the reservation.
