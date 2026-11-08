**Terminal-state errors say why** — `SandboxTerminalStateError` carries the
server's explanation of the state that ended a wait as `reason` (both SDKs),
and its message ends with it, so a deletion that did not finish reads
`sandbox <id> reached terminal state error: <why>` instead of naming only the
state. `daytona` CLI waits that end on `failed` or `lost` print the same
explanation after `reached failed, not <target>`.
