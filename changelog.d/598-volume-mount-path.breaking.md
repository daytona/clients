**Volume mount path rules** — a volume's `mount_path` / `mountPath` (and the
CLI's `--volume VOLUME:MOUNT_PATH`) must be an absolute path with no `..`
component, and may not be `/`, contain a control character, or be on or
beneath `/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/lib32`, `/lib64`,
`/mnt/scratch`, `/overlay`, `/proc`, `/sbin`, `/sys` or `/usr`: the create
fails with `BadRequestError` (400). The API stores the path normalized
(repeated slashes, `.` components and a trailing slash removed). `VolumeMount`
and the READMEs document the rules.
