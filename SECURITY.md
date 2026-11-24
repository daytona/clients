# Security Policy

This repository (the Daytona SDKs and CLI) is covered by
[Daytona's security policy](https://github.com/daytona/.github/blob/main/SECURITY.md),
which sets out scope, response times, safe harbor and disclosure.

## Reporting a vulnerability

Do not report security vulnerabilities through public GitHub issues. Use one of:

- the **Report a vulnerability** button in this repository's
  [Security tab](https://github.com/daytona/clients/security/advisories/new);
- email [security@daytona.io](mailto:security@daytona.io).

## Supported versions

Reports are accepted against the latest release of each line.

| Line | Branch | Packages |
|---|---|---|
| 2.x | `main` | `daytona` (PyPI) and `@daytona/sdk` (npm) as installed by default, the Go SDK `go.daytona.com/sdk/v2`, and the `daytona` CLI |
| 0.x | `release/v0` | 0.x versions of `daytona` (PyPI) and `@daytona/sdk` (npm), the legacy `daytona-sdk` / `@daytonaio/sdk` packages, the 0.x Go SDK `github.com/daytona/clients/sdk-go`, and the 0.x CLI |
