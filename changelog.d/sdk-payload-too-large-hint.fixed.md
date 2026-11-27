**The payload-too-large hint fits the call** — a response over the client's
`max_response_bytes` / `maxResponseBytes` now says to raise the client's
limit, except on the calls that take a per-call limit: `read_bytes()` /
`read_text()` / `read_many()` (`readBytes()` / `readText()` / `readMany()`)
suggest a higher per-call value or `download_file()` / `downloadFile()`, and
the recording download suggests a higher per-call value. Every call used to
suggest a per-call option most calls do not have.
