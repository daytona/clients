import { Daytona, Sandbox, Volume, VolumeType } from '@daytona/sdk'

// The hotmount bootstrap (init.sh) needs `curl`. Daytona's standard images ship with it;
// this makes the example work on minimal snapshots too.
async function ensureCurl(sandbox: Sandbox): Promise<void> {
  const res = await sandbox.process.executeCommand(
    'command -v curl >/dev/null 2>&1 || (command -v apt-get >/dev/null 2>&1 && ' +
      'apt-get update -qq && apt-get install -y -qq curl ca-certificates) || ' +
      '(command -v apk >/dev/null 2>&1 && apk add --no-cache curl ca-certificates)',
  )
  if (res.exitCode !== 0) {
    throw new Error(`Failed to ensure curl is installed: ${res.result}`)
  }
}

// Poll until a freshly created hotmount volume finishes provisioning (org + routing
// created on the region's control-server). Only a `ready` volume can mint mount tokens.
async function waitUntilReady(daytona: Daytona, volume: Volume, timeoutMs = 60_000): Promise<Volume> {
  const start = Date.now()
  let current = volume
  while (current.state !== 'ready') {
    if (current.state === 'error') {
      throw new Error(`Volume ${current.name} failed to provision: ${current.errorReason ?? 'unknown error'}`)
    }
    if (Date.now() - start > timeoutMs) {
      throw new Error(`Timed out waiting for volume ${current.name} to become ready (state: ${current.state})`)
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
    current = await daytona.volume.get(current.name)
  }
  return current
}

async function main() {
  const daytona = new Daytona()

  // List the hotmount regions available to your organization.
  // A region is fixed for the volume's lifetime, so pick one up front.
  const regions = await daytona.volume.listHotmountRegions()
  console.log('Available hotmount regions:', regions)

  // Pick a region close to where the workload runs. This dev environment is in
  // Europe, so prefer the EU region (fall back to whatever is available).
  const region = (regions.find((r) => r.geo === 'eu') ?? regions[0])?.region
  console.log('Using hotmount region:', region)

  // Create a hotmount volume (or fetch it if it already exists).
  // Hotmount volumes are network-attached POSIX filesystems that are
  // mounted into a running sandbox on demand. They support synchronous
  // multi-writer access, so several sandboxes can mount the same volume
  // concurrently.
  const volume = await daytona.volume.get('my-hotmount-shared-demo', true, {
    type: VolumeType.HOTMOUNT,
    region,
  })
  console.log('Volume:', volume.id, 'region:', volume.region, 'shared:', volume.shared)

  // Wait for the volume to finish provisioning before mounting it.
  await waitUntilReady(daytona, volume)
  console.log('Volume is ready')

  // Hotmount volumes are not attached at create time. Start a sandbox first,
  // then mount the volume into it wherever you like.
  const mountDir = '/home/daytona/hotmount'
  const sandbox1 = await daytona.create({ language: 'typescript' })
  await ensureCurl(sandbox1)
  await sandbox1.mountVolume(volume, mountDir)
  console.log('Mounted volume in sandbox1')

  // Write a file through the hotmount filesystem.
  const newFile = `${mountDir}/hello.txt`
  await sandbox1.fs.uploadFile(Buffer.from('Hello from hotmount!'), newFile)
  console.log('Wrote hello.txt via sandbox1')

  // Mount the same volume in a second sandbox: it reads the same network filesystem,
  // so it sees the file written by sandbox1 (once the write-back has drained to the gateway).
  const sandbox2 = await daytona.create({ language: 'typescript' })
  await ensureCurl(sandbox2)
  await sandbox2.mountVolume(volume, mountDir)
  console.log('Mounted volume in sandbox2')

  let contents: Buffer | undefined
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      contents = await sandbox2.fs.downloadFile(newFile)
      break
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 1000))
    }
  }
  console.log('Files visible in sandbox2:', await sandbox2.fs.listFiles(mountDir))
  console.log('File contents read by sandbox2:', contents ? contents.toString() : '<not visible yet>')

  // Cleanup
  await daytona.delete(sandbox1)
  await daytona.delete(sandbox2)
  // await daytona.volume.delete(volume)
}

main()
