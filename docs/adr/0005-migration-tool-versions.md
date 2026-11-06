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
It targets that SDK release, never downgrading a newer declaration (a pre-release range above it is pinned to its own floor, since a range would also admit the stale `2.0.0-rc.1`; one exception: while the target is a pre-release, a range floored on its own stable release, such as the pre-lockstep `^2.0.0` / `>=2,<3`, installs nothing yet and is replaced): a pre-release is pinned (`==2.0.0a8`,
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

**Bump the pin by hand.** Few new 0.x releases are expected, so moving the pin
(and regenerating the tool's v0 data) stays a manual pull request when a
release needs it.

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
- The publish run builds the tool and uploads each package only once its SDK
  version is visible on that registry; the tool's tests run on pull requests.
