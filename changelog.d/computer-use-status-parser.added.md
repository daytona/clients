**Decode computer-use status from your own transport** — apps that reach the
sandbox toolbox through their own transport can decode a status response with
the SDK's own rules instead of copying them:
`parseComputerUseStatus(body)` from `@daytona/sdk/v2` and
`parse_computer_use_status(body)` from `daytona.v2` take the decoded JSON
value, and `daytona.ParseComputerUseStatus(body)` in Go takes the response
bytes. Each returns what `computerUse.status()` returns for the same body,
including the unknown status/desktop pair, and a body that breaks the
contract is the same 400 `BAD_REQUEST` error (`BadRequestError` in
TypeScript and Python, a `*daytona.Error` in Go).
