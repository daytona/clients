**Default image** — `create()` with no image boots the platform default image
only where the deployment configures one; otherwise it fails with
`DEFAULT_IMAGE_NOT_CONFIGURED` (400), so register an image first.
