**CLI: `image build` checks where the Dockerfile resolves** — a Dockerfile that
resolves outside a directory context is a usage error before any upload, and a
file system error reading it is reported as such.
