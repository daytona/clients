**One text for build-log control events without `on_event` / `onEvent`** —
state, gap, unavailability, source-change and error events reach `on_logs` /
`onLogs` as `[build logs: <type>] ` followed by the event's wire fields as
compact JSON (`type` first, absent fields left out), the same line in Python
and TypeScript, for example
`[build logs: state] {"type":"state","status":"ready"}`. Python printed the
event object, and TypeScript used camelCase field names.
