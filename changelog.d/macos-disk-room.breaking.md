**macOS disk room per Mac model:** `MacModel.sandbox_disk_mib`
(`sandboxDiskMib` in TypeScript) changes on each model: `m4-10c-16g` 81920 MiB
(was 90112), `m4pro-12c-24g` 282624 MiB (was 225280), `m4pro-12c-48g` 241664
MiB (was 200704) and `m5pro-15c-24g` 293888 MiB (was 236544). The default disk
on `m4-10c-16g` is 80 GiB (was 88 GiB), and that model takes no persistent
sandbox. On `m4pro-12c-24g`, the default disk is 122 GiB when two sandboxes
share the Mac.
