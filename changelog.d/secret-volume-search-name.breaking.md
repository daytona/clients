**`search` is reserved for secret and volume search** — both SDKs and the CLI
now reject creating or looking up a secret or volume named exactly `search`
before sending a request, because those paths identify the search routes.
