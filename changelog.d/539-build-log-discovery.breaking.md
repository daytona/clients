**Build-log discovery is strict UTF-8** — both SDKs decode the build-log
discovery response as strict UTF-8: invalid UTF-8 or a leading byte order mark
is the protocol error "invalid build log discovery JSON"
(`BUILD_LOG_PROTOCOL_ERROR`). Python accepted a byte order mark and UTF-16 /
UTF-32 bodies, and TypeScript replaced invalid bytes with U+FFFD.
