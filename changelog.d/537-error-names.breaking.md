**One set of v2 error names** — the v2 error classes have the same names in
both SDKs: Python `UnauthorizedError` becomes `AuthenticationError`, and
TypeScript `AuthorizationError` → `ForbiddenError` and `TimeoutError` →
`ClientTimeoutError`. The legacy synonyms `DaytonaNotFoundError`,
`DaytonaAuthenticationError`, `DaytonaAuthorizationError`,
`DaytonaConflictError`, `DaytonaValidationError`, `DaytonaRateLimitError`,
`DaytonaTimeoutError`, `DaytonaConnectionError`, `DaytonaNotSupportedError`
and the `…V2` aliases of the first eight are removed from `daytona.v2` / `@daytona/sdk/v2`
(use `NotFoundError`, `AuthenticationError`, `ForbiddenError`,
`ConflictError`, `BadRequestError`, `RateLimitError`, `ClientTimeoutError`,
`ConnectionError`, `NotSupportedError`); `DaytonaError` / `DaytonaErrorV2`
stay. The root drops its `DaytonaValidationError` and
`DaytonaAuthorizationError` aliases: catch `DaytonaBadRequestError` /
`DaytonaForbiddenError` (Python's `ImportError` for either now names that
category as well as `daytona.v0`). Python callers should import the v2 errors by name
(not `from daytona.v2 import *`): `ConnectionError` and `FileNotFoundError`
mirror the SDK's categories and shadow the built-ins of the same name.
The migration tool's `daytona-v2:` note on a 0.x import of either former root
alias names the root category to catch instead.
