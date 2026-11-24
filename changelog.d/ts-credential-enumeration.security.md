**TypeScript: credentials are not enumerable** — the API key, JWT and sandbox
access tokens are no longer enumerable properties of the client, the
`Sandbox` handle, its namespaces' transports or the token providers, and
SDK-built HTTP clients no longer keep the bearer in their default headers, so
object spread, `Object.keys` / `Object.entries`, deep-clone helpers and error
reporters that walk objects do not pick them up. `sandbox.accessToken` still
returns the sandbox token.
