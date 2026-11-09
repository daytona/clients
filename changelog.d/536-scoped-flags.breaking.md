**CLI: flags only where they apply** — `--file/-f`, `--no-wait`, `--timeout`
and `--region` exist only on the commands that use them; elsewhere they are
unknown flags (exit 2) instead of being accepted and ignored. `--region` stays
on `sandbox create*`, `sandbox list`, `sandbox search`, `image list`, `image
create`, `image build`, `reservation create`, `cpu-type list` and `mac-model
list`.
