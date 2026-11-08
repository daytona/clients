**CLI build contexts honour `.dockerignore`** — `image build --context DIR`
no longer uploads what the context's ignore file excludes (Docker's rules,
including `<Dockerfile>.dockerignore`, `!` exceptions and `**`), and the
64 MiB limit counts only what is uploaded. A tar context is sent as is;
`sandbox cp` is unchanged.
