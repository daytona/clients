# 0005. Migration tool versions

Status: implemented

## Context

`daytona-upgrade` (npm `@daytona/upgrade`, PyPI `daytona-upgrade`) moves 0.x
code onto SDK 2.x's `.v0` namespace ([0004](0004-version-namespaces-and-v0-migration.md)).
Its correctness depends on two things: the 2.x SDK it writes into the
project, and the 0.x release that SDK bundles as `.v0`. Its mapping is checked
against one commit's v2 surface and one pinned 0.x release, and nothing else.

Two questions follow. Which SDK release should a given tool release install?
And what should it do with a project on a 0.x version other than the one
`.v0` runs?

## Decision

**One version for the tool and the SDKs.** The tool is published by the SDK
publish workflow, in the same run and at the same version, after both SDKs.
It writes exactly that SDK release: a pre-release is pinned (`==2.0.0a8`,
`"2.0.0-alpha.8"`), because pre-releases may break; a stable `X.Y.Z` writes
`>=X.Y.Z,<X+1` / `^X.Y.Z`. The SDKs' runtime guidance for 0.x calls names the
tool release of their own version. A tool fix ships with the next SDK release.

**Compare the project's 0.x version with `.v0`.** The tool reads the installed
0.x version from the project's lockfile, else from its manifest's lower
bound:

- the `.v0` release: nothing to report;
- older, or a newer patch of the same minor: a warning, not a manual item;
- a newer minor: refuse (exit 2) unless `--allow-newer-v0`, because 0.x APIs
  added after the `.v0` release are missing from it and get no FIXME;
- unknown: a warning.

**Keep the pin current.** A scheduled workflow opens a pull request that moves
the pin to each new 0.x release and regenerates the tool's v0 data, so a newer
minor stays rare.

## Alternatives

**A separately versioned tool.** One tool release would have to support a
range of SDK releases, with a compatibility matrix nothing tests. A tool
version independent of the SDK also says nothing about which SDK it installs.

**A `^2.0.0` range.** It never matches a pre-release, so during the alpha
phase migrated projects could not install at all; and `^2.0.0-alpha.N`
resolves to the stale published `2.0.0-rc.1`.

**Accept only the pinned 0.x release.** Almost every project would be refused:
0.x keeps releasing, and most projects are on another version. **Accept any
0.x version silently.** A project on a newer minor would lose APIs without a
FIXME.

## Consequences

- Users run the tool release that matches the SDK they want
  (`npx @daytona/upgrade@<version>`, `uvx daytona-upgrade@<version>`); the
  unversioned `latest` of the tool only moves with a stable SDK release.
- A publish run with the tool checks the packed tool end to end against that
  run's SDK artifacts before any approval.
- Pin bump pull requests need a maintainer: approve the run and add mapping
  rows for new 0.x surface.
