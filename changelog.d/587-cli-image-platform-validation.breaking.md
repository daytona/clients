**CLI: refuse invalid `image list --platform` values** — `daytona image list
--platform` takes `true`, `false` or `any`; any other value is rejected with
exit 2 before any request.
