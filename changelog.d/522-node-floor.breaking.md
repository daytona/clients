**TypeScript: Node.js 22.15 or newer** — `@daytona/sdk` now declares
`engines.node >=22.15.0` (was `>=22.4.0`). The bundled 0.x SDK's
OpenTelemetry 3.x dependencies declare Node 22.15, so older Node 22 releases
get `EBADENGINE` warnings from npm. Browsers and other runtimes are unaffected.
