**macOS image capture is ready at once:** a capture of a stopped persistent
macOS sandbox is `ready` when the request returns; it has no `building` state.
An image larger than the organization's image size limit answers
`409 IMAGE_TOO_LARGE` at the request, with
`Image exceeds the maximum size of <limit>. Contact support to raise your organization's image size limit.`
No macOS request answers `409 SNAPSHOT_IN_PROGRESS` now.
