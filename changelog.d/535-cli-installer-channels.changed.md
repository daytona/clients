**CLI installers pick the highest stable release** — `install.sh` and
`install.ps1` without a version install the highest stable 2.x release by
version, not the first 2.x release GitHub lists, so a later alpha or a
backport published after a higher release is no longer installed by default.
From `2.0.0` on, pre-releases come only with `--channel rc|alpha`
(`DAYTONA_CHANNEL`) or an exact `--version` (`DAYTONA_VERSION`); until then,
with no stable 2.x release to install, the default installs the highest
pre-release and says so. The README fetches the
installers from the `main` branch
(`https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh`,
`…/install.ps1`) instead of a fixed release.
