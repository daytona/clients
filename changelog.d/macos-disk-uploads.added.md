**macOS disk uploads:** New error `DISK_UPLOAD_PENDING` (`409`, source
`DAYTONA_API`, `ConflictError` in both SDKs) refuses a macOS start, fork or
snapshot while the sandbox's newest changes are still uploading from the Mac it
last ran on; retry it. `MacModel.sandbox_disk_mib`
(`sandboxDiskMib` in TypeScript) is the disk room for sandboxes on one Mac of
the model, counting their disks, their disk volumes and 8192 MiB for each
persistent sandbox's saves; it is zero until the model's disk room is measured.
