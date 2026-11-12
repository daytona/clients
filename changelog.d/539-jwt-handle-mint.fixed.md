**JWT handles mint their own sandbox token** — in TypeScript, a handle of a
client configured with `jwtToken` and `projectId` now mints a sandbox token
for its toolbox calls, as one with an API key does and as Python already did;
it sent the project token instead, unless the handle came from a create
response. A deployment without the mint route (405 / 501) still falls back to
the project token, and a caller-supplied `accessToken` is never replaced.
