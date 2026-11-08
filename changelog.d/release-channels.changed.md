**Release channels** — each release's channel follows from its version:
`X.Y.Z-alpha.N` publishes to npm `alpha` and Homebrew `daytona-alpha`,
`X.Y.Z-rc.N` to npm `next` and Homebrew `daytona-rc`, and a stable `X.Y.Z` to
npm `latest`, Homebrew `daytona` and a normal CLI GitHub Release. The first
stable 2.x release moves `brew install daytonaio/cli/daytona` and npm
`latest` from the 0.x line to 2.x; the 0.x CLI stays available as
`daytona@0` (`daytona-v0`).
