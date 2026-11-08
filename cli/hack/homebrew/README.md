# Homebrew formulae

The formulae live in the tap,
[`daytonaio/homebrew-cli`](https://github.com/daytonaio/homebrew-cli), and are
changed there. This repository keeps no copy of them.

| Formula | Follows | Tap workflow (event) | Updated by |
|---|---|---|---|
| `daytona` | stable `vX.Y.Z` | `brew_publish.yaml` (`update-version`) | the 0.x line until the first stable 2.x release, then this line |
| `daytona-rc` | `vX.Y.Z-rc.N` | `brew_publish_rc.yaml` (`update-version-rc`) | this line, from the first 2.x rc on (until then it holds an older release candidate from another repository) |
| `daytona-alpha` | `vX.Y.Z-alpha.N` | `brew_publish_alpha.yaml` (`update-version-alpha`) | this line |
| `daytona@0` | stable `v0.X.Y`, as `daytona-v0` | `brew_publish_v0.yaml` (`update-version-v0`) | the 0.x line |

`.github/workflows/sdk_publish.yaml` derives the formula, event and tap
workflow from the version (`scripts/release-channel.sh`) and dispatches one
event per CLI release. What it relies on, checked by its gate before anything
is built:

- `<formula>.rb` exists on the tap's default branch;
- the tap workflow runs on `repository_dispatch` with that event type, updates
  `<formula>.rb`, and names its run after the dispatched version
  (`run-name: … ${{ github.event.client_payload.version }}`), which is how the
  publish job finds and waits for its run.

Before dispatching, the publish job reads `<formula>.rb`: a formula never moves
to a lower version, so a release below the version it names (an older rc or
alpha; the gate already refuses a stable version below the highest stable
2.x release) leaves it alone, and the same version (a resumed run) is
only read back. The tap's `scripts/update_sha.sh` refuses a lower version too,
once the tap's 2.x routing change is merged (before the first 2.x rc or stable
release; RELEASING.md, "The first stable release").
After the run, the publish job reads `<formula>.rb` back and fails unless its
`version` is the published one and every `url` downloads from this
repository's release of it (`https://github.com/daytona/clients/releases/download/v#{version}/…`). If the job's token cannot list the tap's
runs, it skips waiting for the run and instead reads the formula back for up
to 15 minutes, failing unless it names the published version; the formula
update is verified either way. The tap's `scripts/update_sha.sh` accepts only
the version shape of each formula, so a dispatch meant for one channel never
moves another.
