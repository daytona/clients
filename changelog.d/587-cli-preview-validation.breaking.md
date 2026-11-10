**CLI: validate preview-url arguments locally** — on `preview-url expire`, a
blank token is a usage error, and on `preview-url` and `preview-url expire` a
port outside 1-65535 is a usage error, before any request.
