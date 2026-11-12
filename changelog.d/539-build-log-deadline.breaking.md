**Build-log deadlines are RFC 3339 timestamps** — a build-log state event
whose `deadline_at` is not a string, or not an RFC 3339 timestamp, is the
build-log protocol error (`BUILD_LOG_PROTOCOL_ERROR`), as every malformed
build-log event of a known name is, in both SDKs.
