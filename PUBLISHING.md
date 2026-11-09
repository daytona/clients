# Publishing Daytona SDKs

This document describes how to publish the Daytona SDKs (Python, TypeScript, and Ruby) to their respective package registries.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Python SDK (PyPI)](#python-sdk-pypi)
- [TypeScript SDK (npm)](#typescript-sdk-npm)
- [Ruby SDK (RubyGems)](#ruby-sdk-rubygems)
- [Automated Publishing (CI/CD)](#automated-publishing-cicd)
- [Version Management](#version-management)

## Prerequisites

Before publishing any SDK, ensure you have:

1. **Maintainer Access**: Write access to the Daytona repository
2. **Package Registry Credentials**:
   - PyPI: Token with upload permissions
   - npm: none for CI — publishing uses [trusted publishing](https://docs.npmjs.com/trusted-publishers) (OIDC); a granular token scoped to `@daytonaio/sdk` is needed only for the `npm deprecate` step
   - RubyGems: none for CI — publishing uses trusted publishing (OIDC)
3. **Local Development Setup**:
   - All dependencies installed (`yarn install`)
   - SDKs built successfully
   - Tests passing

## Python SDK (PyPI)

### Using Nx

```bash
# From repository root
export PYPI_TOKEN="your-pypi-token"
export PYPI_PKG_VERSION="X.Y.Z" # pre-release format example: "X.Y.Za1"
yarn nx publish sdk-python
```

**Note**: [Guide](https://packaging.python.org/en/latest/discussions/versioning/) for versioning Python packages.

## TypeScript SDK (npm)

In CI, npm packages are published through npm trusted publishing (OIDC) from the `sdk_publish.yaml` workflow on a GitHub-hosted runner. The same nx targets also work locally against a logged-in npm CLI (`npm login`); expect a 2FA prompt per `npm publish`, and no provenance attestation on locally published versions:

```bash
# From repository root
npm login
export NPM_PKG_VERSION="X.Y.Z" # pre-release format example: "X.Y.Z-alpha.1"
export NPM_TAG="latest"  # or "beta", "alpha", etc.
export NPM_DEPRECATE_TOKEN="..."  # optional; otherwise run `npm deprecate` for @daytonaio/sdk by hand
yarn nx publish sdk-typescript
```

Each package has a trusted publisher configured for organization `daytona`, repository `clients`, workflow `sdk_publish.yaml`, environment `release`, with `npm publish` and `npm dist-tag` allowed:

- `@daytona/sdk`, `@daytona/api-client`, `@daytona/toolbox-api-client`, `@daytona/analytics-api-client`
- legacy mirrors `@daytonaio/sdk`, `@daytonaio/api-client`, `@daytonaio/toolbox-api-client`

OIDC does not cover `npm deprecate`, so the deprecation of each new `@daytonaio/sdk` version uses the granular `NPM_DEPRECATE_TOKEN` secret (read/write on `@daytonaio/sdk` only). Because of that token, `@daytonaio/sdk` must keep "allow tokens that bypass 2FA" in its publishing access settings; every other package can disallow tokens.

**Note**: NPM packages must have [SemVer-aligned formats](https://semver.org/).

## Ruby SDK (RubyGems)

In CI, gems are published through RubyGems [trusted publishing](https://guides.rubygems.org/trusted-publishing/) (OIDC) from the `sdk_publish.yaml` workflow. Each gem (`daytona`, `daytona_api_client`, `daytona_toolbox_api_client`, `daytona_analytics_api_client`) has a trusted publisher for owner `daytona`, repository `clients`, workflow `sdk_publish.yaml`, environment `release`.

The same nx targets also work locally against a signed-in `gem` CLI (`gem signin`); expect an MFA prompt per `gem push`:

```bash
# From repository root
gem signin
export RUBYGEMS_PKG_VERSION="X.Y.Z" # pre-release format example: "X.Y.Z.alpha.1"
yarn nx publish sdk-ruby
```

**Note**: [Guide](https://guides.rubygems.org/patterns/#prerelease-gems) for versioning Ruby gems.

## Automated Publishing (CI/CD)

### GitHub Actions Workflow

The repository includes a GitHub Actions workflow for automated publishing: `.github/workflows/sdk_publish.yaml`

#### Triggering a Release

1. Go to **Actions** → **SDK and CLI Publish** in the GitHub repository
2. Click **Run workflow** and select `release/v0` under **Use workflow from**
3. Fill in the parameters:
   - **version**: The version to release (e.g., `v0.126.0`)
   - **pypi_pkg_version**: (Optional) Override PyPI version
   - **npm_pkg_version**: (Optional) Override npm version
   - **rubygems_pkg_version**: (Optional) Override RubyGems version
   - **npm_tag**: npm dist-tag (default: `latest`; see [Release Branches](#release-branches) for maintenance releases)

#### Required Secrets

Ensure these secrets are configured in GitHub repository settings:

- `PYPI_TOKEN`: PyPI API token
- `NPM_DEPRECATE_TOKEN`: granular npm token scoped to `@daytonaio/sdk`, used only by `npm deprecate` (all packages publish via trusted publishing)
- `GITHUBBOT_TOKEN`: GitHub token for Homebrew tap updates

### What the Workflow Does

1. Checks out the code
2. Sets up all required environments (Go, Java, Python, Node.js, Ruby)
3. Installs dependencies
4. Configures credentials for all package registries
5. Runs `yarn publish` which uses Nx to publish all SDKs in the correct order
6. Updates the Homebrew tap (for the CLI)

Every run of **SDK and CLI Publish** and of **Release** waits for one approval on the `release` environment. Only one job per workflow uses the environment: in **SDK and CLI Publish** it is the `publish` matrix job, with one leg per SDK plus a `CLI (Homebrew tap)` leg that dispatches the tap update, and the legs wait together, so one approval releases them all. Each of these workflows, and **Prepare Release**, first runs a `plan` job without credentials whose summary (the inputs and what the run will do) is on the run page while the approval is pending; read it before approving.

The **Release** workflow builds the CLI binaries and uploads them to the GitHub release; the `release` job opens the `go.sum` sync PR as its last step. The `attest_cli` job then downloads the binaries from the release, checks they match the SHA-256 sums the `release` job recorded at build time, and attests them (SLSA build provenance). If an asset was replaced after the upload, the check fails and nothing is attested. The attestation records the GitHub-hosted runner that signed the binaries, not the Blacksmith runner that built them, so `--deny-self-hosted-runners` passes even though the build ran on a self-hosted runner. If only `attest_cli` fails, for example during a Sigstore outage, use **Re-run failed jobs** on the same run. It does not re-run the release, and a re-run from `release.yaml` still passes `gh attestation verify --signer-workflow daytona/clients/.github/workflows/release.yaml`. `attest_cli` still runs when a `release` step after the CLI upload (SDK docs, `go.sum` sync) fails. Do not re-run the `release` job for such a failure: upload the docs, or open the `go.sum` sync PR by hand from your own account (a PR created with a workflow's `GITHUB_TOKEN` triggers no CI): on a `sync-gosum-vX.Y.Z` branch from the released commit, run `GONOSUMDB=github.com/daytona/clients go work sync`, commit with `-s`, and open the PR against the branch the release ran from. Before running the Homebrew dispatch, check one binary:

```bash
gh release download vX.Y.Z --repo daytona/clients --pattern daytona-linux-amd64
gh attestation verify ./daytona-linux-amd64 --repo daytona/clients \
  --signer-workflow daytona/clients/.github/workflows/release.yaml
```

For a 0.x release cut from `release/v0` after the [cutover](#cutting-over-to-the-2x-line), the binaries are named `daytona-v0-<os>-<arch>`: check `daytona-v0-linux-amd64` instead.

### Release Branches

The 0.x line is maintained on `release/v0`. `main` carries the 2.x line, which has a different layout and its own release workflows, so 0.x changes are not made on `main` and cherry-picked: they land directly on `release/v0` through a PR against it. Releases are tags (`v0.224.2`, `sdk-go/v0.224.2`), never branches.

To ship a 0.x release, run **Prepare Release**, **Release** and **SDK and CLI Publish** with `release/v0` selected in **Use workflow from**. On a `release/vN` branch the workflows:

- refuse versions outside the branch's line (`release/v0` only releases `v0.x.y`);
- open the prepare-release and `go.sum` sync PRs against that branch;
- re-mark the highest stable release as **Latest** on GitHub after creating the release, because the CLI update check reads it;
- refuse `npm_tag: latest` unless the version is above every published stable version (`hack/npm-latest-guard.sh`), and fail after publishing if `latest` is not the highest stable version; use a line tag such as `v0-latest` instead. A package never published to npm must be published once before a release branch can publish it with `latest`;
- never update the Homebrew tap, except `release/v0` after the [cutover](#cutting-over-to-the-2x-line), which updates the `daytona@0` formula, and also the `daytona` formula until the first stable 2.x release ([below](#the-daytona-formula-until-2x-is-stable)).

PyPI, RubyGems and the Go module proxy resolve the newest version by version order, not by publish date, so a maintenance release does not replace the newest release there. Maven and Gradle builds declare an exact version, and version ranges resolve by version order. The Go vanity site ([`hack/go-vanity`](hack/go-vanity/README.md)) deploys from `release/v0`.

### Cutting over to the 2.x line

Now that the 2.x SDK and CLI are the repository's main line, the 2.x CLI takes the `daytona` binary name and Homebrew formula, and the 0.x CLI moves aside to `daytona-v0` (formula `daytona@0`) so both install side by side. The 0.x workflows on `release/v0` do this behind one switch, the repository variable `DAYTONA_V0_CUTOVER`. While it is unset (or anything but `true`) they behave as described above and the 0.x CLI stays `daytona`.

At the cutover, in this order:

1. Merge the `daytona@0` formula into the Homebrew tap ([`cli/hack/homebrew`](cli/hack/homebrew/README.md)).
2. Set the variable:

   ```bash
   gh variable set DAYTONA_V0_CUTOVER --body true --repo daytona/clients
   ```

3. Cut a stable `v0.X.Y` release from `release/v0` (not a prerelease: the `daytona@0` tap job accepts only stable versions): **Release**, then **SDK and CLI Publish** with **publish_cli**. This is the first release with `daytona-v0` assets and `install-v0.sh`, and its tap update gives `daytona@0` its first installable version.
4. Only then point users at `daytona@0` and `install-v0.sh`.

From then on, runs from `release/v0`:

- **Release** builds the CLI as `daytona-v0` (its help, hints and `mcp config` name `daytona-v0`, and the upgrade hint names `daytonaio/cli/daytona@0`), uploads `daytona-v0-<os>-<arch>` assets, `install-v0.sh` and `SHA256SUMS`, attests the `daytona-v0-*` binaries, and keeps re-marking the highest stable release, now 2.x, as **Latest**;
- **SDK and CLI Publish** with **publish_cli** dispatches `update-version-v0` to the tap for stable `v0.X.Y` versions (prereleases fail the tap job). With `npm_tag: latest` it refuses to publish once a stable 2.x release exists (`hack/stable-v2-release.sh`): from then on publish with `v0-latest`. Until then npm `latest` keeps following 0.x, like the `daytona` formula below, and `hack/npm-latest-guard.sh` still keeps it on the highest published stable version.

#### The `daytona` formula until 2.x is stable

Until the first stable 2.x release exists, `brew install daytonaio/cli/daytona` keeps installing the 0.x CLI, so every stable 0.x release moves both formulae. A stable 2.x release is a published GitHub release, neither a draft nor a prerelease, tagged `vX.Y.Z` with X ≥ 2 (`hack/stable-v2-release.sh`). 2.x prereleases such as `v2.0.0-alpha.7` do not count. Until one exists, runs from `release/v0` also:

- **Release**: upload the 0.x binaries a second time as `daytona-<os>-<arch>`, the asset names the `daytona` formula downloads. They are the same bytes as the `daytona-v0-*` assets. When the binary runs as `daytona`, its help, hints and `mcp config` say `daytona` and its upgrade hint names `daytonaio/cli/daytona`; under any other name it stays `daytona-v0`. `SHA256SUMS` lists both names and `attest_cli` attests both;
- **SDK and CLI Publish** with **publish_cli**: dispatch `update-version` (the `daytona` formula) after `update-version-v0` (`daytona@0`), once it has checked that the release carries the `daytona-<os>-<arch>` assets;
- **SDK and CLI Publish** with `npm_tag: latest`: move the npm `latest` dist-tag to the 0.x release (still only above every published stable version).

Both workflows read the repository's releases on every run and log the decision. If the releases cannot be read, the run fails rather than guess. **SDK and CLI Publish** checks again right before it dispatches `update-version`, so a stable 2.x release published while the `daytona@0` tap run is in progress is never moved back to 0.x. The check and the dispatch are still two separate calls, so when you cut the first stable 2.x release, do not run a 0.x **SDK and CLI Publish** with **publish_cli** at the same time. If one did overlap, re-run the 2.x tap update (`update-version` for the 2.x version) once the 0.x run has finished, so the 2.x update lands last. The tap update dispatches one event at a time and waits for each tap run to succeed before sending the next, because the tap's workflows commit to its default branch without rebasing. The first stable 2.x release (`v2.0.0`) ends the period with no manual step: later 0.x releases ship only `daytona-v0-*` assets and update only `daytona@0`. From then on, the `daytona` formula follows 2.x, so the 2.x **SDK and CLI Publish** on `main` must dispatch `update-version` for stable 2.x versions.

The CLI keeps its config directory: 0.x writes `config.json`, 2.x writes `config.yaml` and `credentials.yaml`, so profiles survive the rename. The 0.x update check compares against the newest stable 0.x release, never the 2.x **Latest**. Users move to the renamed CLI with `brew install daytonaio/cli/daytona@0`, or with `install-v0.sh` from a 0.x release (Linux/macOS; on Windows, download `daytona-v0-windows-<arch>.exe`). Releases cut before the flip keep their `daytona-<os>-<arch>` assets.

Only runs from `release/v0` act on the variable; runs from `main` or any other ref ignore it, so the 2.x CLI keeps the `daytona` name.

Deleting the variable is safe only before the first 0.x release after the flip. Once a release has shipped `daytona-v0` assets and the tap has moved `daytona@0` onto them, deleting it would bring back a 0.x `daytona` binary that collides with 2.x and would stop updating `daytona@0`. After that point, leave the variable set and fix forward with a new 0.x release.

## Version Management

### Version Format

`MAJOR.MINOR.PATCH` releases follow semantics:

- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

Prerelease formats depend on SDK language:

1. For **Typescript** (npm) follow semantic versioning ([SemVer](https://semver.org/)): `MAJOR.MINOR.PATCH`

   For pre-releases, use:

   - `0.126.0-alpha.1` - Alpha release
   - `0.126.0-beta.1` - Beta release
   - `0.126.0-rc.1` - Release candidate

2. For **Python** (PyPI) follow Python packages versioning [guide](https://packaging.python.org/en/latest/discussions/versioning/):

   For pre-releases, use:

   - `1.2.0a1` - Alpha release
   - `1.2.0b1` - Beta release
   - `1.2.0rc1` - Release candidate

3. For **Ruby** (gem) follow Ruby gems versioning [guide](https://guides.rubygems.org/patterns/#prerelease-gems):

   For pre-releases, use:

   - `0.126.0.alpha.1` - Alpha release
   - `0.126.0.beta.1` - Beta release
   - `0.126.0.rc.1` - Release candidate

### Checking Published Versions

#### PyPI

```bash
pip index versions daytona
# or
curl -s https://pypi.org/pypi/daytona/json | jq -r .info.version
```

#### npm

```bash
npm view @daytona/sdk version
# or
npm info @daytona/sdk
```

#### RubyGems

```bash
gem search daytona --remote --exact
# or
gem info daytona --remote
```

## References

- [Semantic Versioning](https://semver.org/)
- [Python packages versioning](https://packaging.python.org/en/latest/discussions/versioning/)
- [Ruby gems versioning guide](https://guides.rubygems.org/patterns/#prerelease-gems)
