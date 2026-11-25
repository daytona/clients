**Following an image's build logs by name** — `images.follow_build_logs` /
`images.followBuildLogs` / `Images().FollowBuildLogs` given an image name
failed with `BUILD_LOG_PROTOCOL_ERROR` ("invalid build log URL"), because the
log URL names the image by id. The SDKs now resolve the name to its id with the
API and still refuse a URL that names any other image.
