**One `codes` catalog** — both SDKs expose the known error codes as one
read-only `codes` namespace with the same members: `API_ERROR_CODES`,
`DAEMON_ERROR_CODES`, `GENERIC_ERROR_CODES`, `PROXY_ERROR_CODES`,
`ROUTER_ERROR_CODES`, `CODE_STATUS`, `API_CODE_EXTRAS`, `DAEMON_CODE_EXTRAS`,
`PROXY_CODE_EXTRAS`, `ROUTER_CODE_EXTRAS` and `is_known_error_code` /
`isKnownErrorCode`. TypeScript: `import { codes } from '@daytona/sdk/v2'`
replaces the flat `API_ERROR_CODES`, `CODE_STATUS`, `isKnownErrorCode`, …
exports (the code types move too: `codes.ErrorCode`, `codes.ApiErrorCode`, …),
and its data is frozen. Python: `daytona.v2.codes` holds frozensets
and read-only mappings; the `*_CODES` sets are renamed `*_ERROR_CODES` and the
per-code string constants (`codes.NOT_FOUND`, …) are removed — compare
`error.code` with the string.
