**`daytona image build --context` up to 1 GiB** — a context over 64 MiB is
uploaded straight to storage instead of through the API, so a directory or tar
up to 1 GiB now builds.
