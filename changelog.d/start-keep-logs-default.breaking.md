**`start()` sends one `keep_logs` default** — `process.start()` and
`session.start()` send `keep_logs="on_exit_ttl"` / `keepLogs: 'on_exit_ttl'`
when none is given (the default `run()` already used), so the record and log
stay for a while after the process exits whether or not the process is named.
Before, the SDKs sent nothing and the sandbox kept a named process's log
`until_cleanup` and an unnamed one's `on_exit_ttl`. Pass `"until_cleanup"` to
keep it until the process is deleted.
