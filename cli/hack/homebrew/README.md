# Homebrew tap: the `daytona@0` formula

`0001-daytona-at-0.patch` adds the formula for the 0.x CLI line to the
[daytonaio/homebrew-cli](https://github.com/daytonaio/homebrew-cli) tap. The tap
is a separate repository: apply the patch there, in a PR against its default
branch.

```sh
git clone git@github.com:daytonaio/homebrew-cli.git && cd homebrew-cli
git am /path/to/clients/cli/hack/homebrew/0001-daytona-at-0.patch
```

The patch adds:

- `daytona@0.rb` (class `DaytonaAT0`): downloads the `daytona-v0-<os>-<arch>`
  assets of a `v0.X.Y` release and installs them as `daytona-v0`, so it installs
  next to the `daytona` formula. Its version and checksums are placeholders until
  the first `update-version-v0` dispatch fills them in.
- `.github/workflows/brew_publish_v0.yaml`: listens on `repository_dispatch`
  `update-version-v0`, accepts only `v0.X.Y`, and runs `scripts/update_sha.sh`
  on `daytona@0.rb`.
- `scripts/update_sha.sh`: `daytona@0.rb` takes only bare `0.X.Y` versions and
  reads the `daytona-v0-*` assets; the other formulae are unchanged.
- `README.md`: the `daytona@0` channel.

`sdk_publish.yaml` on `release/v0` dispatches `update-version-v0` once the
repository variable `DAYTONA_V0_CUTOVER` is `true` (see
[PUBLISHING.md](../../../PUBLISHING.md#cutting-over-to-the-2x-line)). Merge the
tap PR before the first 0.x release after the cutover, or that release's tap
update finds no workflow listening.
