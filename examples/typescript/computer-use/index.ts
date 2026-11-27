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
 * Statuses of a final recording (`stop()` returns one) whose file is usable.
 * Any other one is not: `failed` has no file, `interrupted` may be partial,
 * and a status this release does not know is treated the same way.
 */
const USABLE_RECORDING: readonly string[] = ['completed', 'duration_limit_reached', 'size_limit_reached']

interface Timing {
  readonly waitSeconds: number
  readonly pollSeconds: number
}

/** A duration from the environment; exits on a value that is not a finite, non-negative number. */
function seconds(name: string, fallback: number): number {
  const raw = process.env[name] ?? ''
  if (raw === '') return fallback
  const value = Number(raw)
  if (!Number.isFinite(value) || value < 0) {
    console.error(`${name} must be a non-negative number of seconds, got ${JSON.stringify(raw)}`)
    process.exit(2)
  }
  return value
}

/** Poll the desktop until it is ready; resolve to whether it got there. */
async function waitForDesktop(sandbox: Sandbox, timing: Timing): Promise<boolean> {
  const deadline = Date.now() + timing.waitSeconds * 1000
  for (;;) {
    const status = await sandbox.computerUse.status()
    if (status.desktop === 'ready') return true
    if (status.desktop === 'failed' || Date.now() >= deadline) {
      fail(`desktop is ${status.desktop}${status.cause ? `: ${status.cause.message}` : ''}`)
      return false
    }
    await sleep(timing.pollSeconds)
  }
}

/** Record a few desktop actions; resolve to whether the recording is usable. */
async function driveAndRecord(sandbox: Sandbox, timing: Timing): Promise<boolean> {
  section('desktop')
  await sandbox.computerUse.start()
  if (!(await waitForDesktop(sandbox, timing))) return false
  info('desktop ready')

  const started = await sandbox.computerUse.recording.start({ label: `example-${RUN}` })
  info(`recording ${started.id} started`)
  try {
    await sandbox.computerUse.mouse.move({ x: 200, y: 200 })
    await sandbox.computerUse.keyboard.type({ text: 'hello from daytona' })
    const shot = await sandbox.computerUse.screen.screenshot()
    info(`screenshot: ${shot.sizeBytes} bytes`)
  } catch (error) {
    // Stop the recording, but keep the error that ended the actions.
    await sandbox.computerUse.recording.stop({ id: started.id }).catch((stopError: unknown) => {
      fail(`could not stop recording ${started.id}: ${String(stopError)}`)
    })
    throw error
  }
  const recording = await sandbox.computerUse.recording.stop({ id: started.id })

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
  const timing: Timing = {
    waitSeconds: seconds('EXAMPLE_DESKTOP_WAIT_SECONDS', 120),
    pollSeconds: seconds('EXAMPLE_POLL_SECONDS', 2),
  }
  const daytona = client()

  const sandbox = await daytona.create(image, { name: `computer-use-${RUN}`, waitSeconds: 200 })
  info(`created sandbox id=${sandbox.id} state=${sandbox.state}`)

  let ok = false
  try {
    assertStarted(sandbox)
    ok = await driveAndRecord(sandbox, timing)
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
