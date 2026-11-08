**Build-log streams and discovery** — `follow_build_logs()` /
`followBuildLogs()` read the stream's media type case-insensitively, so a
`Text/Event-Stream` response is followed rather than refused as a protocol
error, and both SDKs decode the discovery response as strict UTF-8: invalid
UTF-8 or a leading byte order mark is the protocol error "invalid build log
discovery JSON" (Python accepted a byte order mark and UTF-16, TypeScript
replaced invalid bytes).
