**Release channels** — each release's channel follows from its version:
`X.Y.Z-alpha.N` publishes to npm `alpha` and Homebrew `daytona-alpha`,
`X.Y.Z-rc.N` to npm `next` and Homebrew `daytona-rc`, and a stable 2.x
`2.Y.Z` to npm `latest`, Homebrew `daytona` and a normal CLI GitHub Release
(stable 0.x releases publish from the 0.x line to Homebrew `daytona@0` and,
once 2.x is stable, npm `v0-latest`). The first
stable 2.x release moves `brew install daytonaio/cli/daytona` and npm
`latest` from the 0.x line to 2.x; the 0.x CLI stays available as
`daytona@0` (`daytona-v0`). 2.x publishes no stable backports: fixes ship in
a release above the highest stable one.
