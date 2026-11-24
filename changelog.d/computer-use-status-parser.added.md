**Decode computer-use status from your own transport** — apps that reach the
sandbox toolbox through their own transport can decode a status response body
with the SDK's own rules instead of copying them:
`parseComputerUseStatus(body)` from `@daytona/sdk/v2`,
`parse_computer_use_status(body)` from `daytona.v2` and
`daytona.ParseComputerUseStatus(body)` in Go return what
`computerUse.status()` returns for the same body, including the unknown
status/desktop pair and the `BadRequestError` for a body that breaks the
contract.
