**macOS image capture is ready at once:** a capture of a stopped persistent
macOS sandbox is `ready` when the request returns, with no `building` state. If
the image is larger than the organization's image size limit, the request
answers `409 IMAGE_TOO_LARGE` with `image exceeds the maximum size of <limit>`.
No macOS request answers `409 SNAPSHOT_IN_PROGRESS` now, because no capture
stays in progress.
