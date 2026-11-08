**Platform-service arguments are checked** — in both SDKs, before any request,
with `BadRequestError`: `docker.wait_until_ready()` / `waitUntilReady()`
checks its timeout's type (a Python integer too large for a float means no
limit, as for the other waits); `reservations.update()` needs a boolean
`auto_renew` / `autoRenew`; `reservations.create()` / `create_bulk()`
(`createBulk()`) check that `region` and `model` are strings (a blank region
means no region, and a blank model the region's default model, as the contract
says), `auto_renew` / `autoRenew` and the counts (`count` 1 to 100,
`min_count` / `minCount` 1 to `count`), so none is silently left out of the
request; `volumes.delete()` takes a name or id (not a `Volume`);
`ports.expose()` needs a boolean `public`; `create_access_token()` /
`createAccessToken()` operations must be strings and its `ports` a list of
port numbers; `secrets.set()` needs a string `value`, a string `description`
when given, a boolean `if_not_exists` / `ifNotExists` (a TypeScript `'false'`
sent `If-None-Match: *`) and a list of strings as `allowed_domains` /
`allowedDomains`; a `volumes.create()` name must not be blank. TypeScript: an
omitted required options object (`reservations.create()` / `createBulk()` /
`update()`, `sandbox.volumes.pull()`, `updateLifecyclePolicy()`,
`followBuildLogs()`) is refused.
