**Build-log followers skip events they do not know** — a build-log stream event
whose name this release does not know (one a later API adds) is skipped,
unparsed, and does not move the resume cursor, as file watches skip theirs; it
used to end the follow with `BUILD_LOG_PROTOCOL_ERROR`. Malformed events of a
known name are still that protocol error.
