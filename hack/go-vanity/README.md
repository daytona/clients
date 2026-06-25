# Go vanity import setup (`go.daytona.io/*`) via GitHub Pages

Serves the Go module **vanity import path** `go.daytona.io/<pkg>` for the Go modules in
this repo, hosted on **GitHub Pages**. Deploy is automated by
`.github/workflows/deploy-pages.yml` (runs on every push to `main`).

> `go.daytona.io` is a **placeholder** domain — swap it when you pick the real one
> (see "Changing the domain"). It must be a host you control and can point at Pages.

## Deploy now, set up DNS later (recommended flow)

You can deploy and validate **before** touching DNS:

1. **Enable Pages**: repo **Settings → Pages → Build and deployment → Source = "GitHub Actions"**.
2. **Merge to `main`** (or run the workflow manually). The workflow generates the site
   and deploys it. With no custom domain it is served at the **default GitHub URL**:

   ```
   https://daytona.github.io/clients/
   ```

   (Project pages are always `https://<owner>.github.io/<repo>/`.)

3. **Validate the meta is served** (no DNS needed):

   ```bash
   curl -s "https://daytona.github.io/clients/sdk-go?go-get=1" | grep go-import
   # -> <meta name="go-import" content="go.daytona.io git https://github.com/daytona/clients">
   curl -s "https://daytona.github.io/clients/anything/deep?go-get=1" | grep go-import   # 404.html catch-all
   ```

   This confirms the deployment, generator output, and meta content.

4. **What you can't test until DNS**: a real `go get go.daytona.io/sdk-go`. The go tool
   matches the **module path host** (`go.daytona.io`) against the URL it fetches, so it
   only resolves once `go.daytona.io` actually points at Pages. The default
   `daytona.github.io` URL can't stand in for it (the served `go-import` prefix is
   `go.daytona.io`, which wouldn't match a `daytona.github.io/...` request).

## Going live with the custom domain

1. Add DNS: `go.daytona.io` CNAME → `daytona.github.io`.
2. Set the workflow env `EMIT_CNAME: "true"` (writes the `CNAME` file into the site) and
   set the custom domain in **Settings → Pages**. Re-run the workflow.
3. Enable **Enforce HTTPS** (go requires HTTPS).
4. Verify:

   ```bash
   curl -s "https://go.daytona.io/sdk-go?go-get=1" | grep go-import
   GOFLAGS=-mod=mod go get go.daytona.io/sdk-go@latest   # in a scratch module
   ```

## How it works

`go get go.daytona.io/sdk-go` → `GET https://go.daytona.io/sdk-go?go-get=1` → reads
`<meta name="go-import" content="go.daytona.io git https://github.com/daytona/clients">`.
The prefix is the bare domain, so one meta covers every module and sub-package; the path
after the prefix maps to the repo subdir, versioned by the tag `<pkg>/vX.Y.Z`.

## Module → import path → tag

| Dir | Module path | `go get` | Tag |
|---|---|---|---|
| `sdk-go/` | `go.daytona.io/sdk-go` | `go get go.daytona.io/sdk-go` | `sdk-go/vX.Y.Z` |
| `api-client-go/` | `go.daytona.io/api-client-go` | `go get go.daytona.io/api-client-go` | `api-client-go/vX.Y.Z` |
| `toolbox-api-client-go/` | `go.daytona.io/toolbox-api-client-go` | `go get go.daytona.io/toolbox-api-client-go` | `toolbox-api-client-go/vX.Y.Z` |
| `cli/` | `go.daytona.io/cli` | n/a — installed via Homebrew / release binaries¹ | n/a |

¹ The CLI bakes in config (API URL, Auth0) via linker flags in `cli/hack/build.sh`, so a
plain `go install` would produce a non-functional binary. It is shipped as release
assets + the Homebrew tap, not via `go install`, and is not git-tagged for module use.

## `go.work` bootstrap replace (pre-publish only)

Until those modules are tagged and the vanity host is live, their pinned versions can't
resolve over the network, so `go.work` carries versioned `replace` directives pointing
them at local dirs:

```
replace go.daytona.io/api-client-go v0.190.0 => ./api-client-go
replace go.daytona.io/toolbox-api-client-go v0.190.0 => ./toolbox-api-client-go
```

These live only in `go.work` (not published — consumers never see them). Remove or bump
them once the deps are tagged + the vanity host is live. If you bump a consumer's
required version, update the matching version here too.

## Changing the domain

```bash
grep -rl 'go.daytona.io' . --exclude-dir=node_modules --exclude-dir=.git \
  | xargs sed -i 's#go\.daytona\.io#NEW.DOMAIN#g'
# then re-run the go.work replaces, `go work sync`, and set DOMAIN in the workflow env.
```

## Local preview

```bash
bash hack/go-vanity/generate-site.sh           # default github.io mode (no CNAME)
EMIT_CNAME=true bash hack/go-vanity/generate-site.sh   # custom-domain mode
# output in hack/go-vanity/site/
```
