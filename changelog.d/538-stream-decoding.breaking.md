**Compressed streams are decoded in bounded steps** — in Python, a streamed
response (process log streams, directory watches, `download_file()`, build
logs) compressed with gzip (also sent as `x-gzip`) or deflate is decoded by
the SDK at most 64 KiB at a time, as capped reads are, never by the HTTP
client, so one small compressed chunk can no longer expand into a large event
before the stream's bound is checked. TypeScript runtimes already decode a
stream in small chunks.
