**CLI: build-context parts are bounded by progress** — `image build` no longer
fails a context part after five minutes while it is still uploading; a part
fails (exit `8`, after its retries) only once no bytes move for 60 seconds.
