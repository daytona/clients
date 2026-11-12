**macOS disk space per Mac model** — `MacModel.sandbox_disk_mib` is the disk
space a Mac of the model has for sandbox disks and disk volumes. It is zero
until the model's disk space is measured. It changes on each model: `m4-10c-16g`
81920 MiB (was 90112), `m4pro-12c-24g` 282624 MiB (was 225280), `m4pro-12c-48g`
241664 MiB (was 200704) and `m5pro-15c-24g` 293888 MiB (was 236544). The default
disk is 80 GiB on `m4-10c-16g` (was 88 GiB), and that model takes no persistent
sandbox. The default disk is 122 GiB on `m4pro-12c-24g`.
