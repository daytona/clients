# Go vanity import setup (`go.daytona.com/*`) via GitHub Pages

Serves the Go module **vanity import path** `go.daytona.com/<pkg>` for the Go modules in
this repo, hosted on **GitHub Pages**. Deploy is automated by
`.github/workflows/deploy-pages.yml` (runs on every push to `main`).

## Current setup

- DNS: `go.daytona.com` CNAME → `daytona.github.io`.
- Repo **Settings → Pages**: Source = "GitHub Actions", Custom domain = `go.daytona.com`,
  Enforce HTTPS = on (go requires HTTPS). GitHub Pages serves a project site at the
  **root** of the custom domain, so there is no `/clients/` prefix.
- The workflow bakes `DOMAIN=go.daytona.com` into the meta tag and writes a `CNAME` file
  (`EMIT_CNAME=true`).

Verify:

```bash
curl -s "https://go.daytona.com/sdk-go?go-get=1" | grep go-import
# -> <meta name="go-import" content="go.daytona.com git https://github.com/daytona/clients">
curl -s "https://go.daytona.com/anything/deep?go-get=1" | grep go-import   # 404.html catch-all
GOFLAGS=-mod=mod go get go.daytona.com/sdk-go@latest   # in a scratch module
```

## How it works

`go get go.daytona.com/sdk-go` → `GET https://go.daytona.com/sdk-go?go-get=1` → reads
`<meta name="go-import" content="go.daytona.com git https://github.com/daytona/clients">`.
The prefix is the bare domain, so one meta covers every module and sub-package; the path
after the prefix maps to the repo subdir, versioned by the tag `<pkg>/vX.Y.Z`.

GitHub Pages serves `404.html` (with HTTP 404) for every unmatched path; the go tool
ignores the status code and only parses the meta tags, so `404.html` acts as the
catch-all for every module and sub-package path.

## Module → import path → tag

| Dir | Module path | `go get` | Tag |
| --- | --- | --- | --- |
| `sdk-go/` | `go.daytona.com/sdk-go` | `go get go.daytona.com/sdk-go` | `sdk-go/vX.Y.Z` |
| `api-client-go/` | `go.daytona.com/api-client-go` | `go get go.daytona.com/api-client-go` | `api-client-go/vX.Y.Z` |
| `toolbox-api-client-go/` | `go.daytona.com/toolbox-api-client-go` | `go get go.daytona.com/toolbox-api-client-go` | `toolbox-api-client-go/vX.Y.Z` |
| `analytics-api-client-go/` | `go.daytona.com/analytics-api-client-go` | `go get go.daytona.com/analytics-api-client-go` | `analytics-api-client-go/vX.Y.Z` |
| `cli/` | `go.daytona.com/cli` | n/a — installed via Homebrew / release binaries¹ | n/a |

¹ The CLI bakes in config (API URL, Auth0) via linker flags in `cli/hack/build.sh`, so a
plain `go install` would produce a non-functional binary. It is shipped as release
assets + the Homebrew tap, not via `go install`, and is not git-tagged for module use.

## Versions pinned in `go.mod` only resolve after a release

`sdk-go/go.mod` and `cli/go.mod` pin `go.daytona.com/api-client-go@vX.Y.Z` etc. Those
versions only exist on the network once the release workflow has tagged `<pkg>/vX.Y.Z`
on a commit whose `go.mod` declares the `go.daytona.com/...` module path. Inside this
repo that does not matter: `go.work` `use`s every module, so workspace builds resolve
them locally (the versioned `replace` lines in `go.work` are a leftover of the same
bootstrap and are harmless). `examples/go/go.mod` carries path `replace`s for the same
reason so it can be tidied with `GOWORK=off`.

Consequence: a plain `go mod tidy` inside `sdk-go/` or `cli/` (which ignores `go.work`)
fails until the first release under the new path has been tagged. Use `go work sync`
from the repo root instead.

## Changing the domain

```bash
grep -rl 'go.daytona.com' . --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.nx \
  | xargs sed -i 's#go\.daytona\.com#NEW.DOMAIN#g'
# then `go work sync`, set DOMAIN in the workflow env, update DNS and Settings → Pages.
```

## Local preview

```bash
bash hack/go-vanity/generate-site.sh                   # default github.io mode (no CNAME)
EMIT_CNAME=true bash hack/go-vanity/generate-site.sh   # custom-domain mode
# output in hack/go-vanity/site/
```
