**Preview warning skip header constants** — `daytona.v2` and `@daytona/sdk/v2`
export `PREVIEW_WARNING_SKIP_HEADER` (`X-Daytona-Skip-Preview-Warning`) and
`PREVIEW_WARNING_SKIP_HEADER_VALUE` (`true`), so code that opens a preview URL
with a headless browser can send the header that skips the preview warning page
without copying the literal.
