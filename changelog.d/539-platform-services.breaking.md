**Platform services are plural** — the client accessors are `volumes` (was
`volume`) and `secrets` (was `secret`; Python also had `secrets` as an alias
of `secret`, which is gone). The service classes are renamed to match: Python
`AsyncImageService`, `AsyncSnapshotService`, `AsyncVolumeService`,
`AsyncSecretService`, `AsyncReservationService` and `AsyncMacModelService`
(and their synchronous twins without `Async`) become `AsyncImagesService`,
`AsyncSnapshotsService`, `AsyncVolumesService`, `AsyncSecretsService`,
`AsyncReservationsService` and `AsyncMacModelsService`; TypeScript
`VolumeService` and `SecretService` become `VolumesService` and
`SecretsService` (its other service classes already had plural names).
