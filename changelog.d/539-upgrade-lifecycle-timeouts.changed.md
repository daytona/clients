**Migration tool: lifecycle suggestions carry only the timeout the call
passed** — the `--suggest-v2` forms of `start()`, `stop()`, `pause()`,
`delete()`, `fork()` and the start/stop waits (client and sandbox) name a 0.x
call's timeout (and a fork's name or parameters) only when the call passed
one, spelled as the call spelled it; a call that used the default gets a form
that keeps v2's default, where the suggestion used to name a `timeout` /
`timeoutSeconds` variable the code did not have. `npx @daytona/upgrade` and
`uvx daytona-upgrade` write the same forms.
