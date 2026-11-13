# Releasing the SDKs

How to publish the two client SDKs and the `daytona` CLI.

> **One version, every target.** The SDKs and the CLI share one version line
> ([VERSIONING.md](VERSIONING.md) "One version line"). A release publishes one
> `vX.Y.Z[-pre.N]` version to PyPI, npm and the CLI's GitHub Release; the
> publish workflow derives each registry's spelling from it and records it as
> the tag `vX.Y.Z[-pre.N]` on the published commit.

> **The only path: on-demand release via CI.** Run
> [`.github/workflows/sdk_publish.yaml`](../.github/workflows/sdk_publish.yaml)
> (Actions → *SDK Publish* → *Run workflow*, or `gh workflow run`). It
> publishes using **OIDC Trusted Publishing** — no tokens, no 2FA/OTP — and
> **commits nothing**: the version is stamped into the manifest inside the
> runner only. There is [no manual publish](#no-manual-publish).

> **The version picks the channel.** The publish workflow accepts
> `vX.Y.Z` (stable), `vX.Y.Z-rc.N` (release candidate) and `vX.Y.Z-alpha.N`
> (alpha), and derives everything channel-specific from it
> ([`scripts/release-channel.sh`](../scripts/release-channel.sh)): the npm
> dist-tag, whether the CLI's GitHub Release is a pre-release, and which
> Homebrew formula follows it ([below](#what-a-version-derives)). A beta, or
> anything else, is refused. Tell users of a pre-release to pin it exactly
> (`pip install daytona==X.Y.ZaK`, `npm install @daytona/sdk@X.Y.Z-alpha.K`)
> rather than `pip install --pre daytona`: PEP 440 sorts `rc` above `a`, and a
> stale `2.0.0rc1` exists on PyPI ([VERSIONING.md](VERSIONING.md), "Release
> channels").

See also: [VERSIONING.md](VERSIONING.md) · [CHANGELOG.md](../CHANGELOG.md) ·
[`changelog.d/`](../changelog.d/) (changelog fragments).

## Release lines and branches

| Line | Branch | Workflows |
|---|---|---|
| 2.x (this SDK) | `main` | `prepare-release.yaml`, `sdk_publish.yaml` (SDKs, CLI and the migration tool) |
| 0.x | `release/v0` | that branch's own `prepare-release.yaml`, `release.yaml`, `sdk_publish.yaml` |

Each line keeps its own copy of the workflows under the **same file names**:
PyPI and npm Trusted Publishing are bound to repository + workflow file name
+ environment, so one registration per package serves every line. Pick the
line with the dispatch ref:

```bash
gh workflow run sdk_publish.yaml --ref main -f version=v2.0.0-alpha.4
gh workflow run release.yaml     --ref release/v0 -f version=v0.228.0
```

**Two refs, on purpose.** The dispatch ref (`--ref`) selects which copy of
the workflow and its helper scripts runs; the `release` environment admits
only `main` and `release/*`, so publishing logic always comes from a
protected branch and nobody can publish through a modified copy of the
workflow on their own branch (the gate also refuses any other dispatch ref up
front, so such a run fails before it builds anything). The `source_ref` input
(default: the dispatch ref) selects which commit is built and published. Its
code runs only in the tokenless build jobs.

On this line, a **stable** version publishes only a commit on `main`; a
**pre-release** may publish any commit, so a feature can ship an alpha for
early users before it merges:

```bash
gh workflow run sdk_publish.yaml --ref main -f version=v2.1.0-alpha.2 -f source_ref=feat/my-feature
```

A 0.x version is refused here. A run whose gate passes and whose builds and
tests are green waits for **one** approval on the `release` environment,
before any publishing credential is used (a run that fails earlier asks for none); the
run summary names both the source commit and the workflow commit. Before
approving, read the gate's **Publish plan** on the run page: the inputs, the
source commit, the channel, the v0 namespace (marked UNVERIFIED for a `v0_tag`
override) and each target's version. The built packages are kept for 7 days:
approve within that, or dispatch again.

### Moving the 2.x line to `main` (done)

The 2.x line moved from `release/v2` to `main` in November 2026; `release/v2`
is retired. The default branch keeps three things the 0.x line still needs
from it: `release.yaml` (a stub, so
`gh workflow run release.yaml --ref release/v0` stays dispatchable; GitHub
only dispatches workflows that exist on the default branch), `CLA.md`
(linked by the CLA check of every line), and
`assets/images/Daytona-logotype-black.png` (loaded from `main` by released
0.x CLIs). The 0.x Pages site (`deploy-pages.yml`) still deploys only from `main`;
change it there before 0.x next needs a deployment. The AX evals are
separate per line: `release/v0` syncs its 0.x prompts from `hack/ax-evals`,
and `main` syncs the 2.x prompts from `scripts/ax-evals`.

## What a version derives

The gate computes these with [`scripts/sdk-version.sh`](../scripts/sdk-version.sh)
(the registry spellings) and
[`scripts/release-channel.sh`](../scripts/release-channel.sh) (the channel),
and rejects anything that is not a release version:

| Target | From `v2.0.0-alpha.3` | Mechanism |
|---|---|---|
| npm `@daytona/sdk` | `2.0.0-alpha.3` | `npm version` + `npm run stamp-version` in the runner's checkout |
| CLI GitHub Release (and tag) | `v2.0.0-alpha.3` | `VERSION=2.0.0-alpha.3 hack/build.sh` (ldflags) |
| PyPI `daytona` | `2.0.0a3` — PEP 440: `-alpha.N` → `aN`, `-rc.N` → `rcN`; stable unchanged | `sed` on `pyproject.toml` in the runner's checkout |

| Version | Channel | npm dist-tag | CLI GitHub Release | Homebrew formula (tap workflow) |
|---|---|---|---|---|
| `vX.Y.Z-alpha.N` | alpha | `alpha` | pre-release | `daytona-alpha` (`brew_publish_alpha.yaml`) |
| `vX.Y.Z-rc.N` | rc | `next` | pre-release | `daytona-rc` (`brew_publish_rc.yaml`) |
| `vX.Y.Z` | stable | `latest` | release; **Latest** when it is the repository's highest stable release | `daytona` (`brew_publish.yaml`) |

The migration tool takes the SDKs' dist-tag. The 0.x line keeps its own
`daytona@0` formula and publishes from `release/v0`.

**2.x has no stable backports.** The gate refuses a stable version below the
highest stable 2.x release (a GitHub Release, or the `v2.Y.Z` tag an SDK-only
run records) before anything is built, in every mode
(`force_republish` included); the same version is a resume. A fix for an
older minor ships in a release above the highest one. npm `latest`, GitHub's
Latest and the `daytona` formula therefore only move forward; the checks
below hold that independently too. (Allowing backports later, for example
under an npm line tag like the 0.x line's `v0-latest`, would be an addition.)
Every stable run also shares one concurrency group, so two stable versions
never publish at once.

The formulae live in the tap
[`daytonaio/homebrew-cli`](https://github.com/daytonaio/homebrew-cli)
([`cli/hack/homebrew/README.md`](../cli/hack/homebrew/README.md)). The gate
checks that the tap has the channel's formula and a workflow that takes its
event and names its run after the version; after the tap run, the publish job
reads the formula back and fails unless it names the published version and
downloads every asset from this repository's release of it.

A formula never moves to a lower version. Before dispatching, the publish job
reads the channel's formula: if it names a higher version (alpha < rc <
stable for the same `X.Y.Z`), the formula is left alone and the job succeeds.
This check reads the formula itself, so it holds independently of the gate:
the gate's no-backport rule reads the releases and tags, not the formula. For
the rc and alpha formulae it is what keeps a formula on a newer pre-release
when an older one publishes later (pre-releases may publish any commit). If
the formula names the same version (a resumed run), the job only reads it
back. With the tap's 2.x routing change merged (see
[below](#the-first-stable-release-200)), the tap's `scripts/update_sha.sh`
refuses a lower version too. At the end of a stable run that publishes the
CLI, GitHub's Latest is pinned on the highest stable release again (a 0.x
`release.yaml` run re-pins Latest as well; the Homebrew step waits for
running ones). A stable run without the CLI creates no GitHub Release, so it
does not touch Latest.

## Automated release (recommended)

One-time setup (see the header of
[`sdk_publish.yaml`](../.github/workflows/sdk_publish.yaml)):

- **Registries:** PyPI trusted publishers for `daytona` and `daytona-upgrade`
  and npm trusted publishers for `@daytona/sdk` and `@daytona/upgrade`, all
  bound to repository `daytona/clients`, workflow file **`sdk_publish.yaml`**
  and environment **`release`**.
  Renaming either breaks publishing (OIDC 403) until both registries are
  updated.
- **Repo:** Settings → Environments → `release` → required reviewers on,
  deployment branches `main` and `release/*` only. Environment secret
  `GITHUBBOT_TOKEN` (the Homebrew formula dispatch).

The workflow is split into tokenless **build** jobs (which run the
install/test/build and upload an artifact) and one **publish** job (which
holds `id-token: write`, `contents: write` and the environment, never checks
out source, and only uploads the built artifacts), so code under test never
sees a publishing credential. The publish job is the only job on the
`release` environment, so a run asks for exactly one approval, after every
requested build is green; a failed or cancelled build skips it (nothing is
published, no approval is asked). Inside it the targets run in order — PyPI,
npm, the migration tool (PyPI, then npm, each once the SDK version it pins is
visible on that registry), the CLI's GitHub Release, the Homebrew formula —
and a failed target stops the ones after it. A failed tool upload therefore
also holds back the CLI: re-run the publish job to finish it (targets already
published are skipped). The
environment's deployment-branch policy still decides whether a run's
credentials can be used at all.

For each release:

1. **Prepare** (stable and release-candidate versions): run
   `prepare-release.yaml` with the version. It folds every fragment in
   [`changelog.d/`](../changelog.d/) into a new `## [X.Y.Z] — DATE` section of
   `CHANGELOG.md` and opens the release PR. Merge it. Alphas may skip this
   step; their changes stay as fragments until the next prepared release.
2. **Publish** from the commit to release:

```bash
gh workflow run sdk_publish.yaml --ref main -f version=v2.0.0-alpha.3
# optional: -f publish_pypi=false / -f publish_npm=false / -f publish_cli=false / -f publish_upgrade=false  (all default true)
# optional: -f force_republish=true      # resume a half-landed publish of this version (see below)
gh run watch "$(gh run list --workflow sdk_publish.yaml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

- `source_ref` picks the commit to build (default: the dispatch ref).
- The four `publish_*` booleans pick the targets; all four publish by
  default so the SDKs, the CLI and the migration tool stay on the same number.
  See [The migration tool](#the-migration-tool) for what `publish_upgrade`
  needs: untick it for a run that does not publish an SDK and is not meant
  to publish the tool (a CLI-only run, an SDK resume), or the gate or the
  registry check refuses it.
- The gate refuses a version a target registry **already has** (npm, PyPI,
  this repository's releases), and a version whose tag already names another
  commit. `force_republish=true` skips the registry check to *resume* a run
  that half-landed (say PyPI succeeded and npm failed): targets already on
  their registry are skipped — PyPI `skip-existing`, an npm pre-check, the
  CLI release's identical-checksums resume — and only the missing ones are
  published. Re-running the failed publish job of the same run (*Re-run
  failed jobs*, one more approval) resumes the same way. Nothing is ever
  overwritten; registries refuse re-uploads regardless.
- A run cancelled while it created the CLI release can leave a **draft**
  release under the tag (`gh release create` uploads the assets to a draft and
  publishes it at the end). The resume refuses a draft, because it cannot
  tell who made it. Before deleting it, check that it came from the failed
  run, by its commit and its assets:

  ```bash
  gh release view v<version> --repo daytona/clients --json isDraft,targetCommitish,assets
  # targetCommitish: the source commit in that run's Summary
  gh run download <run id> --repo daytona/clients --name cli-dist --dir /tmp/cli-dist
  gh release download v<version> --repo daytona/clients --dir /tmp/draft
  (cd /tmp/draft && sha256sum --check --ignore-missing /tmp/cli-dist/SHA256SUMS)
  # every asset the draft has matches the run's build (SHA256SUMS too, if uploaded)
  ```

  If both match, delete the draft
  (`gh release delete v<version> --repo daytona/clients`, keeping the tag if
  it exists) and re-run the failed publish job: the release is created again
  from the same build. If either differs, the draft is not that run's: find
  out who made it before touching it.
- The run's **Summary** page lists what was published to which registry, from
  which branch and commit, with install commands and registry links.

| SDK | Registry | Package | Import / usage name | Version |
|-----|----------|---------|---------------------|---------|
| Python | [PyPI](https://pypi.org/project/daytona/) | `daytona` | `import daytona` | PEP 440 spelling of the version, stamped into `sdk-python/pyproject.toml` at publish |
| TypeScript | [npm](https://www.npmjs.com/package/@daytona/sdk) | `@daytona/sdk` | `from '@daytona/sdk'` | the version without `v`, stamped into `sdk-typescript/package.json` + `src/version.ts` at publish |
| CLI | [GitHub Releases](https://github.com/daytona/clients/releases) + the channel's Homebrew formula | `daytona` | `daytona` | the version, stamped by `hack/build.sh` ldflags at publish; every binary checked with `go version -m` (toolchain at or above `cli/go.mod`'s `go` line) |
| Migration tool (Python) | [PyPI](https://pypi.org/project/daytona-upgrade/) | `daytona-upgrade` | `uvx daytona-upgrade@<version>` | the PyPI spelling, stamped into `upgrade/python/pyproject.toml` at publish |
| Migration tool (TypeScript) | [npm](https://www.npmjs.com/package/@daytona/upgrade) | `@daytona/upgrade` | `npx @daytona/upgrade@<version>` | the npm spelling, stamped into `upgrade/typescript/package.json` at publish |

These five packages are all this line publishes. The legacy names, the PyPI
alias `daytona-sdk` (`daytona_sdk`) and the npm mirror `@daytonaio/sdk`, are
published only from the 0.x line on `release/v0` and never carry 2.x. Their
trusted publishers name the same workflow file and environment, so the
publish job checks the name of every artifact before it uploads it: the PyPI
distributions must be exactly `daytona-<version>` (or `daytona_upgrade-…`),
and each npm tarball's `package.json` must name `@daytona/sdk` (or
`@daytona/upgrade`).

### The migration tool

`daytona-upgrade` is published by the same run as the SDKs, at the same
version, and targets that SDK release in the projects it migrates:

- a pre-release target is pinned (`daytona[v0-sdk]==2.0.0a8`,
  `"@daytona/sdk": "2.0.0-alpha.8"`); a stable `X.Y.Z` writes
  `>=X.Y.Z,<X+1` / `^X.Y.Z`;
- a newer declaration is never downgraded; a pre-release range above the
  target is pinned to its own floor (a range would also admit the stale
  `2.0.0-rc.1`);
- while the target is a pre-release, a range floored on its own stable
  release (the pre-lockstep `^2.0.0` / `>=2,<3`) installs nothing yet and is
  replaced.

Its mapping is checked (by upgrade-tool-ci, on the pull request that changed it)
against the v2 surface and the pinned v0 bundle of the same tree, so a tool
release is correct for its own SDK release only. A pinned
pre-release also keeps migrated projects off the stale `2.0.0-rc.1` /
`2.0.0rc1` that a `^2.0.0-alpha.N` range would resolve to
([VERSIONING.md](VERSIONING.md), "Release channels").

- The tool never publishes without its SDK: with `publish_npm` or
  `publish_pypi` off (a resume), the registry check requires that SDK version
  to be published already, and in the publish job each tool package waits
  until its SDK version is visible on that registry before it uploads, as the
  0.x line's SDKs wait for their client packages.
- The publish run only builds the tool; `upgrade-tool-ci.yaml` tests it on
  every pull request.
- It refuses to publish beside a `v0_tag` override, from a tree without the v0
  namespace, and while the mapping's `docs.placeholder` is true.
- Like the SDKs, a tool pre-release may publish any `source_ref` (a stable
  version only a commit on `main`), behind the same `release` approval; the
  former standalone tool workflow published only commits on `main`.
- Until SDK 2.0.0 is out, the tool's `latest` is the pre-lockstep `0.1.0`:
  tell users to run the exact alpha (`npx @daytona/upgrade@2.0.0-alpha.N`,
  `uvx daytona-upgrade@2.0.0aN`), as the SDKs' guidance and the READMEs do.
- A tool fix ships with the next SDK release. Untick `publish_upgrade` to
  leave the tool out of a run; the SDKs still name the tool release of their
  version (the gate warns and the run summary says so), so publish it later at
  that version from the same commit: `-f publish_pypi=false -f
  publish_npm=false -f publish_cli=false -f source_ref=<that commit>` (the
  registry check requires both SDKs to be published already).

### The migration guide

Every `--suggest-v2` comment and JSON item links `docs.base#<anchor>` from
`upgrade/mapping/mapping.json`. Until the Daytona docs publish the guide,
`docs.base` is the generated `upgrade/MIGRATION.md` on `main` on GitHub, whose
section anchors (`<a id="…">`, one per receiver plus `data`) are checked by
`upgrade/scripts/docs-links.test.mjs`. Published tool releases keep the link
they shipped with, so the in-repo guide and its anchors stay where they are
even after the docs site has its own page: the test's `shippedAnchors` list
only grows, and a guide section the mapping no longer generates keeps its
anchor (`gen-guide.mjs` must still emit it).

**To move the links to the docs site (maintainer step):**

1. Publish the guide at its own page (render `upgrade/MIGRATION.md`; keep every
   section anchor, including `data`, as an element id on that page).
2. Check every anchor resolves there: for each `anchor` in the mapping (and
   `data`), open `<page>#<anchor>`.
3. Set `docs.base` to the page URL (no `#`), update `inRepoGuide` in
   `upgrade/scripts/docs-links.test.mjs` and the READMEs' "Documentation
   links point at …" sentence, run `node upgrade/scripts/sync-mapping.mjs`,
   and release the tool. Never point `docs.base` at the docs home: its anchors
   do not exist there.

### The v0 pin

The tool refuses projects on a newer 0.x minor than the release SDK 2.x
bundles as `.v0` (`scripts/v0-sdk-version`). Bumping the pin is a manual PR,
at least a MINOR release even without a spec change
([VERSIONING.md](VERSIONING.md)). In this order:

1. **Check the new 0.x release first, against the current pin**, before
   anything is written:
   - its runtime floors are not above 2.x's: `requires-python` in its
     `sdk-python/pyproject.toml` against `sdk-python/pyproject.toml` here
     (Python 3.10), and `engines.node` in its `sdk-typescript/package.json`,
     if it declares one, against `sdk-typescript/package.json` here
     (Node 22.15);
   - `.v0` callers stay compatible. Run
     `node upgrade/scripts/check-v0-surface.mjs --src <new 0.x checkout>`
     without `--write-v0-exports`: it compares the new release with the
     export list and client members recorded for the current pin, and lists
     every difference. A `nonexistent` name is one the current pin has and
     the new release dropped, which breaks `.v0` callers: do not move the pin
     to it. A `missing` name is new surface (additions are fine; give new
     methods mapping rows). The tool sees names, not signatures: read the
     0.x changelog between the two tags for changed parameters or behavior.
2. **Move the pin:** write the new tag and the commit it peels to into
   `scripts/v0-sdk-version`, run the same command with `--write-v0-exports`
   (records the new `v0Version` / `v0Exports`), then
   `node upgrade/scripts/sync-mapping.mjs` and
   `node upgrade/scripts/gen-guide.mjs`, and add a changelog fragment.
3. **Check the dependencies the bundle merges.** Start with
   `sdk-typescript/package.json` and `package-lock.json` unmodified (commit
   any edits to them first: the cleanup below restores both from git). Run
   `bash scripts/bundle-v0.sh --lang typescript`, then
   `bash scripts/check-no-bundled-v0.sh --check-list`. It names each runtime
   dependency the new release merges into `sdk-typescript/package.json` that
   the committed manifest does not declare and that is not exempt. Exempt are
   the names in `v0_only` in `scripts/check-no-bundled-v0.sh` (the
   dependencies only v0 uses), v0's API clients (`@daytona/api-client`,
   `@daytona/toolbox-api-client`, `@daytona/analytics-api-client`, which
   the bundle always adds and other checks keep out of the committed
   manifest), and `form-data`, which v2 also resolves. Add each name it
   reports to `v0_only` (and update the release named in its comment) in the
   same pull request. Then discard the bundle
   (`bash scripts/bundle-v0.sh --clean`, then `git checkout --
   sdk-typescript/package.json sdk-typescript/package-lock.json`). CI runs the
   same check on a real bundle.

> **Registries reject re-uploading an existing version.** A version is
> published once. If a publish half-fails, re-run the failed publish job, or
> re-dispatch the same version from the same commit with
> `force_republish=true`, to finish the targets that did not land — do not
> attempt to overwrite.

## The first stable release (2.0.0)

Until a stable 2.x GitHub Release exists, the 0.x line on `release/v0` keeps
three defaults: the `daytona` Homebrew formula, npm `latest` of
`@daytona/sdk`, and GitHub's **Latest** release. Its publish workflows look
for a stable 2.x release on every run (`hack/stable-v2-release.sh` there) and
stop taking those defaults once one exists. The first stable 2.x run hands
all three over in one go, so the gate refuses a first stable run without
`publish_cli`.

Procedure:

1. **Tap.** The tap's 2.x routing change is merged, as a whole, before the
   first 2.x release candidate or stable release publishes. It gives
   `brew_publish.yaml` and `brew_publish_rc.yaml` a `run-name` ending in the
   version (like `brew_publish_alpha.yaml`); makes `scripts/update_sha.sh`
   rewrite every formula URL it updates to `daytona/clients` and refuse a
   lower version than the formula names; and pins the tap workflows' actions
   by commit. Before that change, the live `daytona-rc` formula still names
   an older release candidate from another repository; the first 2.x release
   candidate's tap run rewrites its URLs, and no 2.x publish reads the
   formula back before that. The gate checks the `run-name` and fails the run
   before anything is built without it; the read-back after the tap run fails
   if the formula's URLs do not point at this repository's release.
2. **READMEs.** Open a pull request that switches the published READMEs
   from alpha to stable wording, merge it right before step 3, and publish
   no alpha after it: the READMEs ship with the packages, and before 2.0.0
   a "stable" install command gives users the 0.x line. It changes:
   - `cli/README.md`: the "Alpha" banner ("Versions are `2.0.0-alpha.N`
     until launch"); the installer paragraph's sentence on what happens until
     `2.0.0` is out; the Homebrew table's "(until `2.0.0` is out, the 0.x
     CLI)" and the two commands below it, which become
     `brew install daytonaio/cli/daytona`;
   - `sdk-python/README.md`: the "Alpha" banner, and
     `pip install daytona==X.Y.ZaN` with its `[otel]` / `[v0-sdk]` variants,
     which become `pip install daytona` (`'daytona[otel]'`,
     `'daytona[v0-sdk]'`);
   - `sdk-typescript/README.md`: the "Alpha" banner (including "pin an exact
     `X.Y.Z-alpha.N`"), and `npm install @daytona/sdk@alpha`, which becomes
     `npm install @daytona/sdk`;
   - `upgrade/python/README.md` and `upgrade/typescript/README.md`: "Until
     SDK 2.0.0 is out, run the exact alpha you are migrating to" with its
     `2.0.0aN` / `2.0.0-alpha.N` examples;
   - `sdk-python/pyproject.toml`: the `Development Status :: 3 - Alpha`
     classifier (shown on PyPI).
   - `upgrade/MIGRATION.md`, which is generated: edit the two "Until SDK
     2.0.0 is out, … the exact alpha" paragraphs in
     `upgrade/scripts/gen-guide.mjs` (install commands and tool runs), then
     run `node upgrade/scripts/gen-guide.mjs`; never edit the guide itself.

   Then `node upgrade/scripts/gen-guide.mjs --check` passes, and
   `git grep -n -i alpha -- '*.md' ':!CHANGELOG.md' ':!changelog.d' ':!docs'`
   should list only channel names (`--channel alpha`, `daytona-alpha`) and
   examples of how a pre-release is pinned, no install advice.
3. **Prepare.** Run `prepare-release.yaml` with `v2.0.0` and merge the
   release PR.
4. **No 0.x publish at the same time.** Do not dispatch `release.yaml` or
   `sdk_publish.yaml` on `release/v0` until step 6 is done. The 2.x run also
   waits for running `release/v0` publish runs before it dispatches the
   `daytona` formula update, and reads the formula back afterwards, but a 0.x
   run started in the gap would still have to be undone.
5. **Publish** from the merge commit:

   ```bash
   gh workflow run sdk_publish.yaml --ref main -f version=v2.0.0
   ```

   It publishes PyPI `daytona==2.0.0`, npm `@daytona/sdk@2.0.0` and
   `@daytona/upgrade@2.0.0` under `latest`, `daytona-upgrade==2.0.0`, the CLI
   release `v2.0.0` (a normal release, marked **Latest**), and moves the
   `daytona` formula to `2.0.0`.
6. **Check:**

   ```bash
   npm view @daytona/sdk dist-tags              # latest: 2.0.0
   npm view @daytona/upgrade dist-tags          # latest: 2.0.0
   gh release view --repo daytona/clients --json tagName --jq .tagName   # v2.0.0 (Latest)
   gh api repos/daytonaio/homebrew-cli/contents/daytona.rb --jq .content | base64 -d | grep '^  version'
   curl -fsSL https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh | sh   # installs 2.0.0
   ```

7. **0.x from then on:** `release/v0` publishes update only `daytona@0`, and
   an npm publish there needs `npm_tag: v0-latest` (its workflow refuses
   `latest` once a stable 2.x release exists).

If a 0.x run did overlap and the `daytona` formula shows a 0.x version,
re-run the 2.x run's failed publish job once the 0.x run has finished: it
resumes at the Homebrew update.

## No manual publish

There is no manual publish path. A package built outside
`sdk_publish.yaml` would skip the v0 bundle (`Daytona().v0` would not work),
the checks of the built artifacts, the migration tool's lockstep and the
`release` approval. If a run half-lands, resume it (re-run the failed publish
job, or dispatch the same version from the same commit with
`force_republish=true`); if the workflow itself is broken, fix it in a pull
request.

To reproduce a release build locally (nothing is uploaded), run what the
build jobs of `sdk_publish.yaml` run, including the v0 bundle:

```bash
python -m pip install build                       # the Python build front end (the build job installs it too)
scripts/sdk-version.sh v2.0.0                     # the registry spellings
scripts/release-channel.sh v2.0.0                 # the channel
bash scripts/bundle-v0.sh --lang all              # the pinned v0 namespace (scripts/v0-sdk-version)
(cd sdk-python && python -m build && python ../scripts/check_dist_artifacts.py python dist --v0)
(cd sdk-typescript && npm install && DAYTONA_REQUIRE_V0_BUNDLE=1 npm run build \
  && npm pack --pack-destination pack && python3 ../scripts/check_dist_artifacts.py npm pack --v0)
(cd cli && VERSION=2.0.0 ./hack/build.sh)       # six binaries, SHA256SUMS, go version -m check
```

The build jobs also run both SDKs' suites and smoke tests against the bundle
(`build-python`, `build-npm`). Discard the bundle afterwards
(`git clean -fdX sdk-python/src/daytona/v0 sdk-typescript/src/v0` and
`git checkout -- sdk-python/pyproject.toml sdk-typescript/package.json
sdk-typescript/package-lock.json`).

## Publish checklist

- [ ] The version is right for the branch (`scripts/sdk-version.sh <version>`
      and `scripts/release-channel.sh <version>` show what each target gets)
- [ ] Stable / rc: the prepare-release PR merged before publishing
- [ ] The run summary shows every requested target published
- [ ] The version's tag points at the published commit
- [ ] The first stable 2.x release: [its procedure](#the-first-stable-release-200)

---

## References

- [Automated release](#automated-release-recommended) — the CI workflow, the
  only publish path.
- [VERSIONING.md](VERSIONING.md) — one version line, what the numbers mean,
  the compatibility window.
- PyPI [Trusted Publishing](https://docs.pypi.org/trusted-publishers/).
- npm [Trusted publishing / provenance](https://docs.npmjs.com/generating-provenance-statements).
