**CLI: Check image sources before creating resources** — `image create` refuses
blank `--ref` values, and `image build` refuses directory and plain-tar contexts
missing the selected Dockerfile, with exit 2 before any request. Compressed or
unrecognized archives remain server-validated.
