**`start()` during stop or pause** — `start()` on a sandbox that is `stopping`
or `pausing` waits until it is `stopped` or `paused`. Then it starts or resumes
the sandbox within the same `timeout`. Before, `start()` sent no start request
and waited until the timeout ended.
