**Process results report only what the sandbox reported** — in both SDKs
`WaitResult` always has a `reason`: `'unknown'` for a value this release does
not know (kept on `rawReason` / `raw_reason`) or when the sandbox sent none
(then the raw reason is absent) and its `exitCode` / `exit_code` and `signal`
are optional in every case: TypeScript no longer invents exit code `0` or
signal `KILL`, and a result without a reason reads `'unknown'`, not
`'failed'`. `ProcessResult` and `InterpreterProcess` extend it; Python
`ProcessResult` gains `logs` (the frames the output came from). Signals are
bare names in process metadata too (`ProcessInfo.signal` is `'TERM'`, not
`'SIGTERM'`). `run()`, `session.run()` and `sandbox.exec()` take `RunKeepLogs`
and refuse `keep_logs='none'` (it was silently replaced), since they read the
output back from the log. Type names now match in both SDKs: TypeScript
`ProcessDto` → `ProcessInfo`, `RunResult` → `ProcessResult`, `LogsPage` →
`LogPage`; Python `ProcessInfoKind` → `ProcessReportedKind`, `WaitReason` →
`ProcessReason`. TypeScript `LogPage.truncatedHead` is always set, and a
mistyped `exitCode`, `signal` or `reason` is the malformed-response error
instead of being dropped.
