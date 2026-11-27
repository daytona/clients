/*
 * computer-use — start a sandbox's desktop, drive it, and record it (opt-in).
 *
 * Needs a project image with a desktop, in a region where the platform offers
 * desktop automation; elsewhere the computer-use calls throw
 * `AddonUnavailableError` (503, `ADDON_UNAVAILABLE`).
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... DAYTONA_DESKTOP_IMAGE=<image name or img_ id> \
 *       npx tsx typescript/computer-use/index.ts
 */

import { AddonUnavailableError, type Sandbox } from '@daytona/sdk/v2'
import { assertStarted, client, fail, info, RUN, section, sleep } from '../_common'

/**
 * Recording statuses whose file is usable. Any other one (`failed`,
 * `interrupted`, or a status this release does not know) is not.
 */
const USABLE_RECORDING: readonly string[] = ['completed', 'duration_limit_reached', 'size_limit_reached']
const DESKTOP_WAIT_SECONDS = Number(process.env.EXAMPLE_DESKTOP_WAIT_SECONDS ?? '120')
const POLL_SECONDS = Number(process.env.EXAMPLE_POLL_SECONDS ?? '2')

/** Poll the desktop until it is ready; resolve to whether it got there. */
async function waitForDesktop(sandbox: Sandbox): Promise<boolean> {
  const deadline = Date.now() + DESKTOP_WAIT_SECONDS * 1000
  for (;;) {
    const status = await sandbox.computerUse.status()
    if (status.desktop === 'ready') return true
    if (status.desktop === 'failed' || Date.now() >= deadline) {
      fail(`desktop is ${status.desktop}${status.cause ? `: ${status.cause.message}` : ''}`)
      return false
    }
    await sleep(POLL_SECONDS)
  }
}

/** Record a few desktop actions; resolve to whether the recording is usable. */
async function driveAndRecord(sandbox: Sandbox): Promise<boolean> {
  section('desktop')
  await sandbox.computerUse.start()
  if (!(await waitForDesktop(sandbox))) return false
  info('desktop ready')

  let recording = await sandbox.computerUse.recording.start({ label: `example-${RUN}` })
  info(`recording ${recording.id} started`)
  try {
    await sandbox.computerUse.mouse.move({ x: 200, y: 200 })
    await sandbox.computerUse.keyboard.type({ text: 'hello from daytona' })
    const shot = await sandbox.computerUse.screen.screenshot()
    info(`screenshot: ${shot.sizeBytes} bytes`)
  } finally {
    recording = await sandbox.computerUse.recording.stop({ id: recording.id })
  }

  section('recording')
  // A stop that returns is not a usable file: check the status first.
  if (!USABLE_RECORDING.includes(recording.status)) {
    fail(`recording ${recording.id} ended ${recording.status}; its file is not usable`)
    return false
  }
  const data = await sandbox.computerUse.recording.download(recording.id)
  info(`recording ${recording.fileName}: ${data.byteLength} bytes, status ${recording.status}`)
  return true
}

async function main() {
  const image = process.env.DAYTONA_DESKTOP_IMAGE
  if (!image) {
    console.error('DAYTONA_DESKTOP_IMAGE is required (a project image with a desktop)')
    process.exit(2)
  }
  const daytona = client()

  const sandbox = await daytona.create(image, { name: `computer-use-${RUN}`, waitSeconds: 200 })
  info(`created sandbox id=${sandbox.id} state=${sandbox.state}`)

  let ok = false
  try {
    assertStarted(sandbox)
    ok = await driveAndRecord(sandbox)
  } catch (error) {
    if (!(error instanceof AddonUnavailableError)) throw error
    fail(`desktop automation is not available for this sandbox: ${error.message}`)
  } finally {
    section('cleanup')
    await daytona.delete(sandbox)
    info(`deleted sandbox ${sandbox.id}`)
  }

  process.exit(ok ? 0 : 1)
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
