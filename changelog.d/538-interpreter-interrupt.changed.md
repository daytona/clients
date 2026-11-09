**Interpreter timeouts** — TypeScript returns a timed-out interpreter run
whose result already carries an exit code (the code's own deadline ended it)
without sending the kernel an interrupt first, as Python does.
