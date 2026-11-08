/*
 * build-logs — read the output of a Dockerfile build.
 *
 * Builds a deliberately chatty Dockerfile as a sandbox and prints the build
 * log as text, including each `RUN` step's stdout and build errors.
 *
 * `sandbox.buildLogs()` returns build state, available output and any
 * truncation notice. Use `followBuildLogs()` for live events and completion
 * metadata. The example also reads logs for a second sandbox created from the
 * same Dockerfile, then deliberately fails a build and prints its error output.
 */

import { ImageBuilder, ImageBuildFailedError } from '@daytona/sdk/v2'
import { client, section, info, pass, fail, IMAGE, RUN } from '../_common'

// Distinct, labelled steps make the build output easy to follow.
const DOCKERFILE = `FROM ${IMAGE}
RUN echo "STEP 1: who am i" && id
RUN echo "STEP 2: the marker" && echo build-logs-${RUN} > /marker && cat /marker
RUN echo "STEP 3: done"
`

// `false` always exits non-zero, so this build cannot succeed. A unique marker
// lets us check that the failed command's output is available.
const BROKEN_DOCKERFILE = `FROM ${IMAGE}
RUN printf '%s%s\\n' 'intentional-build-failure-' '${RUN}' && false
`

/** Print a build log with a visible frame, so it stands out from the run. */
function show(logs: string): void {
  console.log('   +' + '-'.repeat(68))
  for (const line of logs.split('\n')) console.log(`   | ${line}`)
  console.log('   +' + '-'.repeat(68))
}

async function main() {
  const daytona = client()
  let ok = true
  const sandboxes: Awaited<ReturnType<typeof daytona.create>>[] = []

  try {
    section('build a Dockerfile as a sandbox')
    const sandbox = await daytona.createFromBuild(ImageBuilder.fromDockerfile(DOCKERFILE), {
      name: `buildlogs-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    sandboxes.push(sandbox)
    info(`sandbox ${sandbox.id} is ${sandbox.state}`)

    section('the build output')
    const logs = await sandbox.buildLogs()
    show(logs)

    // Require the RUN step's output, not merely a build status line.
    if (logs.includes('STEP 2: the marker')) pass("the log contains our RUN step's output")
    else {
      fail("the log did not contain the build's own output")
      ok = false
    }
    if (logs.includes(`build-logs-${RUN}`)) pass('it shows the value the step actually produced')
    else info('the marker value was not present in the available output')

    section('build logs for a second sandbox from the same Dockerfile')
    const twin = await daytona.createFromBuild(ImageBuilder.fromDockerfile(DOCKERFILE), {
      name: `buildlogs-twin-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    sandboxes.push(twin)
    const twinLogs = await twin.buildLogs()
    show(twinLogs)
    if (twinLogs.includes('STEP 2: the marker')) {
      pass("the second sandbox's build log contains the RUN step's output")
    } else {
      fail("the second sandbox's build log is missing the RUN step's output")
      ok = false
    }

    section('a FAILED build keeps its log, which says why')
    info('building something that cannot succeed — this takes ~20s')
    try {
      const broken = await daytona.createFromBuild(ImageBuilder.fromDockerfile(BROKEN_DOCKERFILE), {
        name: `buildlogs-bad-${RUN}`,
        // Bound the build separately from the sandbox readiness wait.
        buildTimeoutSeconds: 120,
        waitSeconds: 120,
      })
      sandboxes.push(broken)
      fail(`the deliberately broken build unexpectedly created ${broken.id} (${broken.state})`)
      ok = false
      show(await broken.buildLogs())
    } catch (error) {
      const message = String(error)
      info(`create failed as expected: ${message}`)
      // A CLIENT timeout is not a build failure: it means the build outlived
      // timeout, so this section proved nothing. Distinguish them, or a slow
      // build turns a silent non-test into a PASS.
      if (/timeout/i.test(message)) {
        fail('the create timed out rather than failing — the build outlived the timeout, so this section proved nothing')
        ok = false
      } else if (!(error instanceof ImageBuildFailedError) || error.imageId === undefined) {
        fail(`expected ImageBuildFailedError carrying the image id, got: ${message}`)
        ok = false
      } else {
        // Use the error's image id to read the failed build's output.
        let failedLog = ''
        let readFailed = false
        try {
          await daytona.images.followBuildLogs(error.imageId, {
            onLogs: (chunk) => {
              failedLog += chunk
            },
            timeoutSeconds: 60,
          })
        } catch (readError) {
          // Reading build logs can fail; report the error to the user.
          fail(`the failed build's log could not be read: ${readError}`)
          ok = false
          readFailed = true
        }
        if (failedLog !== '') show(failedLog)
        // After a read error the output may be partial, so judge only a
        // complete read. A state line alone does not prove output was
        // retrieved: require our failing command's output.
        if (!readFailed) {
          if (failedLog === '') {
            fail("the failed build's log was empty")
            ok = false
          } else if (failedLog.includes(`intentional-build-failure-${RUN}`)) {
            pass("the failed build's log contains the failing command's output")
          } else {
            fail("the failed build's log is missing the failing command's output")
            ok = false
          }
        }
      }
    }
  } finally {
    section('cleanup')
    for (const sandbox of sandboxes) {
      try {
        await daytona.delete(sandbox)
        info(`deleted ${sandbox.id}`)
      } catch (error) {
        // Cleanup must not mask a real failure above.
        info(`could not delete ${sandbox.id}: ${error}`)
      }
    }
  }

  process.exit(ok ? 0 : 1)
}

main().catch((error) => {
  console.error('Error:', error)
  process.exit(1)
})
