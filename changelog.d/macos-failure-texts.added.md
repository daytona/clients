**macOS failure texts:** the sandbox `error` (`errorReason` in TypeScript,
`error_reason` in Python) has new texts. A fork gets
`the fork's copy of its source's disk could not be stored; delete this sandbox and fork its source again`
if its copy of the source's disk fails. A persistent sandbox stops and gets
`the sandbox's disk failed on its Mac; it was stopped and starts again from its last save`
if its disk fails on its Mac. An ephemeral sandbox gets
`the sandbox's disk failed on its Mac` in the same case. A sandbox gets
`the Mac reservation was reissued before a Mac claimed it` if the platform
reissues its Mac reservation before a Mac claims it; before, the text was
`the sandbox's local disk is unavailable; recreate the sandbox`. A create from
a macOS base that its region does not publish yet answers
`503 SERVICE_UNAVAILABLE` with
`no macOS base of image '<image>' is published in region '<region_id>' yet; retry the create in a few minutes`.
