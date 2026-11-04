# Releasing the SDKs

How to publish the two client SDKs and the `daytona` CLI.

> **One version, every target.** The SDKs and the CLI share one version line
> ([VERSIONING.md](VERSIONING.md) "One version line"). A release publishes one
> `vX.Y.Z[-pre.N]` version to PyPI, npm and the CLI's GitHub Release; the
> publish workflow derives each registry's spelling from it and records it as
> the tag `vX.Y.Z[-pre.N]` on the published commit.

> **Preferred path: on-demand release via CI.** Run
> [`.github/workflows/sdk_publish.yaml`](.github/workflows/sdk_publish.yaml)
> (Actions → *SDK Publish* → *Run workflow*, or `gh workflow run`). It
> publishes using **OIDC Trusted Publishing** — no tokens, no 2FA/OTP — and
> **commits nothing**: the version is stamped into the manifest inside the
> runner only. The manual steps further down are the fallback.

> **ALPHA-ONLY until general availability.** The publish workflow
> **hard-rejects any version that is not an alpha pre-release** — no stable,
> rc or beta version can ship by accident. The version must be
> `vX.Y.Z-alpha.K`, which derives Python `X.Y.ZaK` (PEP 440, e.g. `2.0.0a1`)
> and TypeScript / CLI `X.Y.Z-alpha.K` (e.g. `2.0.0-alpha.1`). npm publishes
> under the `alpha` dist-tag (never `latest` — see VERSIONING.md "Release
> channels"), and pip skips PyPI pre-releases unless asked. Tell users to pin
> the exact alpha (`pip install daytona==X.Y.ZaK`) rather than
> `pip install --pre daytona`: PEP 440 sorts `rc` above `a`, and a `2.0.0rc1`
> already exists on PyPI, so `--pre` would resolve that instead of a newer
> alpha. npm users use `npm install @daytona/sdk@alpha`. Manual publishes must
> follow the same rule. Widening the gate (rc, then stable) is done by
> relaxing the alpha-only regexes in `sdk_publish.yaml`'s gate — nothing else:
> the npm dist-tag is derived from the version (`-rc.N` → `next`, stable →
> `latest`), so it follows automatically.

See also: [VERSIONING.md](VERSIONING.md) · [CHANGELOG.md](CHANGELOG.md) ·
[`changelog.d/`](changelog.d/) (per-PR changelog fragments).

## Release lines and branches

| Line | Branch | Workflows |
|---|---|---|
| 2.x (this SDK) | `release/v2` until 2.x is generally available, then `main` | `prepare-release.yaml`, `sdk_publish.yaml`, `upgrade-tool-publish.yaml` |
| 0.x | `release/v0` | that branch's own `prepare-release.yaml`, `release.yaml`, `sdk_publish.yaml` |

Each line keeps its own copy of the workflows under the **same file names**:
PyPI and npm Trusted Publishing are bound to repository + workflow file name
+ environment, so one registration per package serves every line. Pick the
line with the dispatch ref:

```bash
gh workflow run sdk_publish.yaml --ref release/v2 -f version=v2.0.0-alpha.4
gh workflow run release.yaml     --ref release/v0 -f version=v0.228.0
```

**Two refs, on purpose.** The dispatch ref (`--ref`) selects which copy of
the workflow and its helper scripts runs; the `release` environment admits
only `main` and `release/*`, so publishing logic always comes from a
protected branch and nobody can publish through a modified copy of the
workflow on their own branch. The `source_ref` input (default: the dispatch
ref) selects which commit is built and published. Its code runs only in the
tokenless build jobs.

On this line, a **stable** version publishes only a commit on `main` (so
not before 2.x moves there); a
**pre-release** may publish any commit, so a feature can ship an alpha for
early users before it merges:

```bash
gh workflow run sdk_publish.yaml --ref release/v2 -f version=v2.1.0-alpha.2 -f source_ref=feat/my-feature
```

A 0.x version is refused here. Every run waits for a reviewer on the
`release` environment; the run summary names both the source commit and the
workflow commit.

## What a version derives

The gate computes these with [`scripts/sdk-version.sh`](scripts/sdk-version.sh)
and rejects anything that is not a release version:

| Target | From `v2.0.0-alpha.3` | Mechanism |
|---|---|---|
| npm `@daytona/sdk` | `2.0.0-alpha.3` | `npm version` + `npm run stamp-version` in the runner's checkout |
| CLI GitHub Release (and tag) | `v2.0.0-alpha.3` | `VERSION=2.0.0-alpha.3 hack/build.sh` (ldflags) |
| PyPI `daytona` | `2.0.0a3` — PEP 440: `-alpha.N` → `aN`, `-beta.N` → `bN`, `-rc.N` → `rcN`; stable unchanged | `sed` on `pyproject.toml` in the runner's checkout |

## Automated release (recommended)

One-time setup (see the header of
[`sdk_publish.yaml`](.github/workflows/sdk_publish.yaml)):

- **Registries:** a PyPI trusted publisher for `daytona` and an npm trusted
  publisher for `@daytona/sdk`, both bound to repository `daytona/clients`,
  workflow file **`sdk_publish.yaml`** and environment **`release`**.
  Renaming either breaks publishing (OIDC 403) until both registries are
  updated.
- **Repo:** Settings → Environments → `release` → required reviewers on,
  deployment branches `main` and `release/*` only. Environment secret
  `GITHUBBOT_TOKEN` (the Homebrew formula dispatch).

The workflow is split into tokenless **build** jobs (which run the
install/test/build and upload an artifact) and **publish** jobs (which hold
`id-token: write` and the environment, never check out source, and only
upload the built artifact), so code under test never sees a publishing
credential.

For each release:

1. **Prepare** (stable and release-candidate versions): run
   `prepare-release.yaml` with the version. It folds every fragment in
   [`changelog.d/`](changelog.d/) into a new `## [X.Y.Z] — DATE` section of
   `CHANGELOG.md` and opens the release PR. Merge it. Alphas may skip this
   step; their changes stay as fragments until the next prepared release.
2. **Publish** from the commit to release:

```bash
gh workflow run sdk_publish.yaml --ref release/v2 -f version=v2.0.0-alpha.3
# optional: -f publish_pypi=false / -f publish_npm=false / -f publish_cli=false  (all default true)
# optional: -f force_republish=true      # resume a half-landed publish of this version (see below)
gh run watch "$(gh run list --workflow sdk_publish.yaml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

- `source_ref` picks the commit to build (default: the dispatch ref).
- The three `publish_*` booleans pick the targets; all three publish by
  default so the SDKs and the CLI stay on the same number.
- The gate refuses a version a target registry **already has** (npm, PyPI,
  this repository's releases), and a version whose tag already names another
  commit. `force_republish=true` skips the registry check to *resume* a run
  that half-landed (say PyPI succeeded and npm failed): targets already on
  their registry are skipped — PyPI `skip-existing`, an npm pre-check, the
  CLI job's identical-checksums resume — and only the missing ones are
  published. Nothing is ever overwritten; registries refuse re-uploads
  regardless.
- The run's **Summary** page lists what was published to which registry, from
  which branch and commit, with install commands and registry links.

| SDK | Registry | Package | Import / usage name | Version |
|-----|----------|---------|---------------------|---------|
| Python | [PyPI](https://pypi.org/project/daytona/) | `daytona` | `import daytona` | PEP 440 spelling of the version, stamped into `python/pyproject.toml` at publish |
| TypeScript | [npm](https://www.npmjs.com/package/@daytona/sdk) | `@daytona/sdk` | `from '@daytona/sdk'` | the version without `v`, stamped into `typescript/package.json` + `src/version.ts` at publish |
| CLI | [GitHub Releases](https://github.com/daytona/clients/releases) + Homebrew `daytona-alpha` | `daytona` | `daytona` | the version, stamped by `hack/build.sh` ldflags at publish |

> **Registries reject re-uploading an existing version.** A version is
> published once. If a publish half-fails, re-dispatch the same version from
> the same commit with `force_republish=true` to finish the targets that did
> not land — do not attempt to overwrite.

---

# Manual release (fallback)

## 0. Pre-flight (both SDKs)

From the repository root:

```bash
# TypeScript: types, tests, and a clean build must pass
cd typescript && npm install && npm run typecheck && npm test && npm run build && cd ..

# Python: unit tests
cd python && .venv/bin/pytest && cd ..   # or your preferred env
```

Derive the registry versions with the same helper the workflow runs:

```bash
scripts/sdk-version.sh v2.0.0-alpha.3
# version=2.0.0-alpha.3  npm=2.0.0-alpha.3  cli=2.0.0-alpha.3  cli_tag=v2.0.0-alpha.3  pypi=2.0.0a3
```

Use those values below; do not pick a number by hand.

---

## 1. Python → PyPI

### One-time setup

Build tooling lives in a throwaway virtualenv (kept out of the package dir so it
never leaks into the sdist):

```bash
python3 -m venv /tmp/daytona-publish-venv
/tmp/daytona-publish-venv/bin/pip install --upgrade build twine
```

> On Debian/Ubuntu you may first need `sudo apt install python3.12-venv`.
> The venv lives in `/tmp` and does **not** survive a reboot — recreate it when
> gone.

Store a PyPI API token (from <https://pypi.org/manage/account/token/>) in
`~/.pypi.token` (the file should contain just the `pypi-...` string).

### Publish

```bash
cd python

# 1. Stamp the derived version (the `pypi=` value) into the working tree — do not commit it.
#    Edit pyproject.toml -> [project].version = "X.Y.ZaK"  (PEP 440 alpha — see the alpha-only note above)

# 2. Clean + build sdist and wheel.
rm -rf dist build
/tmp/daytona-publish-venv/bin/python -m build

# 3. Validate metadata renders on PyPI.
/tmp/daytona-publish-venv/bin/python -m twine check dist/*

# 4. Upload.
/tmp/daytona-publish-venv/bin/python -m twine upload dist/* \
  -u __token__ -p "$(tr -d '\n\r' < ~/.pypi.token)"
```

Username is literally `__token__`; the password is the token.

Verify:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/daytona/json   # 200
pip install "daytona==X.Y.ZaK"   # the alpha you just published; a bare install finds no stable release
python -c "import daytona; print(daytona.Daytona)"
```

---

## 2. TypeScript → npm

### One-time setup

You must be logged in to npm as a user with publish rights on `@daytona/sdk`.

> **Important — npm auth gotcha:**
> npm now **blocks granular / automation access tokens from directly
> publishing** (see <https://gh.io/npm-gat-bypass2fa-deprecation>). Symptoms:
> `npm publish` returns
> `403 ... Two-factor authentication or granular access token with bypass 2fa
> enabled is required`, and **`--otp` is silently ignored**.
>
> The reliable path is an **interactive browser login** (the default `npm login`
> web flow), which creates a session npm accepts for publishing:
>
> ```bash
> npm login          # opens a browser; complete the login there
> npm whoami         # must print your username
> ```
>
> If you hit `EOTP`, that's good — auth succeeded and it just wants the code
> (npm may email it). Re-run with `--otp=CODE`.

### Publish

```bash
cd typescript

# 1. Stamp the derived version (the `npm=` value) into the working tree — do not commit it.
npm version X.Y.Z-alpha.K --no-git-tag-version  # alpha-only until general availability (updates package.json + lockfile)

# 2. Publish. `prepublishOnly` runs `clean && build` automatically (and build's
#    prebuild re-stamps src/version.ts), so the freshly compiled dist/
#    (ESM + CJS + .d.ts) is what ships.
npm publish --access public --tag alpha

#    If the account requires an OTP, append it (check email/authenticator):
#    npm publish --otp=CODE
```

Notes:
- `publishConfig.access` is `public` in `package.json`, so no `--access` flag
  is needed even though npm defaults new packages to restricted for orgs.
- `dist/` is gitignored; it is (re)built by `prepublishOnly` at publish time and
  included via the `files` allowlist. Never commit `dist/`.
- To ship a pre-built dir without rebuilding, use
  `npm publish --ignore-scripts` (only if you already ran `npm run build`).

Verify:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://registry.npmjs.org/@daytona%2fsdk   # 200
npm view @daytona/sdk@alpha version   # the alpha dist-tag; `latest` is the pre-V2 line and never moves
```

---

## 3. Post-release

Discard the stamped manifests (`git checkout -- python/pyproject.toml
typescript/package.json typescript/package-lock.json
typescript/src/version.ts`) — the CI path does not read the tracked `version`
fields either — and tag the published commit `vX.Y.Z[-pre.N]`.

## Publish checklist

- [ ] The version is right for the branch (`scripts/sdk-version.sh <version>`
      shows what each registry gets)
- [ ] Pre-flight passed (typecheck, tests, build) for both SDKs
- [ ] `twine check` passed (Python, manual path)
- [ ] `npm pack --dry-run` looks right (TS, manual path): includes `dist/` + `README.md`
- [ ] Published + verified install on both registries (and the CLI release,
      when published)
- [ ] Stable / rc: the prepare-release PR merged before publishing
- [ ] The version's tag points at the published commit

---

## References

- [Automated release](#automated-release-recommended) — the CI workflow (top of
  this doc) is the recommended path; the manual steps above are the fallback.
- [VERSIONING.md](VERSIONING.md) — one version line, what the numbers mean,
  the compatibility window.
- PyPI [Trusted Publishing](https://docs.pypi.org/trusted-publishers/).
- npm [Trusted publishing / provenance](https://docs.npmjs.com/generating-provenance-statements).
