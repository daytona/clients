**Build-log discovery is strict UTF-8** — both SDKs decode the build-log
discovery response as strict UTF-8: invalid UTF-8 or a leading byte order mark
is the protocol error "invalid build log discovery JSON"
(`BUILD_LOG_PROTOCOL_ERROR`). Python accepted a byte order mark and UTF-16 /
UTF-32 bodies, and TypeScript replaced invalid bytes with U+FFFD. A build-log
HTTP error body is read as UTF-8 in both SDKs, with a leading byte order mark
dropped and invalid bytes replaced, so its message and code come from the same
bytes (Python also read a UTF-16 or UTF-32 body).
