**Build-log media type** — `follow_build_logs()` / `followBuildLogs()` read
the stream's media type case-insensitively, so a `Text/Event-Stream` response
is followed rather than refused as a protocol error.
