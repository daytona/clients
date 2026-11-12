**macOS failure texts:** `sandbox.error` in both SDKs has new texts:
- A fork that cannot get its source's disk:
  `The fork could not get its source's disk. Delete this sandbox, then fork the source again.`
- A persistent sandbox whose disk fails on its Mac stops:
  `The sandbox's disk failed on its Mac, and the sandbox stopped. Start it again.`
- An ephemeral sandbox whose disk fails on its Mac:
  `The sandbox's disk failed on its Mac. Create a new sandbox.`
- A sandbox whose reservation's Mac fails before the sandbox starts on it:
  `The reservation's Mac failed before this sandbox started on it. If the sandbox is stopped, start it again. If it failed, delete it and try again.`
  Before, the text was `the sandbox's local disk is unavailable; recreate the sandbox`.

A create from a macOS image that its region does not have yet answers
`503 SERVICE_UNAVAILABLE` with
`The macOS image '<image>' is not available in region '<region_id>' yet. Try the create again later.`
