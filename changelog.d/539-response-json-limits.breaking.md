**One JSON depth and number range in both SDKs** — a response or events frame
nested deeper than 600 levels (`JSON_MAX_DEPTH`) is a malformed response
naming the response (Python) or skipped (an events frame), never a raw
`RecursionError`, and nothing the SDK does with response data recurses without
a bound. JSON nested that deep in a build-log event or discovery response is
the build-log protocol error, an events-hub frame nested that deep is skipped
like an undecodable one, and a preview token whose payload is nested that deep
reads as having no expiry. An integer too large for a double, which overflows
to infinity (`10**400`), is refused in a field the SDK maps as a number in
both SDKs: TypeScript's `JSON.parse` reads it as `Infinity`, which the field
check refuses, and Python, which read it as given, refuses it before the model
parse. An integer above 2^53 within a double's range (2^53 + 1, a search
`total` of 2^60) is accepted in both, and TypeScript may round it.
