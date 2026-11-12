/*
 * dynamic-build — create sandboxes from Dockerfiles and read their build logs.
 *
 * Pass an inline Dockerfile as the image source and Daytona builds it and
 * boots the result as the sandbox, in one call — no image to register first.
 *
 * Create two sandboxes from the same Dockerfile, inspect each build log, then
 * change the Dockerfile and verify the new file content. Create durations are
 * reported for information only; no particular timing is required.
 */

import { ImageBuilder } from '@daytona/sdk/v2'
import { assertStarted, client, section, info, pass, fail, IMAGE, RUN } from '../_common'

async function main() {
  const daytona = client()
  let ok = true

  // A unique marker lets us verify the Dockerfile's output in each sandbox.
  const dockerfile = `FROM ${IMAGE}\nRUN echo dynamic-build-${RUN} > /marker\n`
  const sandboxes: Awaited<ReturnType<typeof daytona.create>>[] = []

  try {
    section('create a sandbox straight from a Dockerfile (builds it)')
    let started = Date.now()
    const first = await daytona.createFromBuild(ImageBuilder.fromDockerfile(dockerfile), {
      name: `dynbuild-first-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    sandboxes.push(first)
    assertStarted(first)
    const firstSecs = (Date.now() - started) / 1000
    info(`first create took ${firstSecs.toFixed(1)}s`)

    const marker = await first.process.run('cat /marker')
    info(`marker: ${marker.output.trim()}`)
    if (marker.exitCode === 0 && marker.output.includes(RUN)) {
      pass('the sandbox booted the image built from our Dockerfile')
    } else {
      fail(`the Dockerfile's marker file is missing (exit=${marker.exitCode})`)
      ok = false
    }

    section('build logs')
    const logs = await first.buildLogs()
    info(`build log is ${logs.length} chars`)
    // Text can contain only build state; a nonempty response alone does not
    // prove that the Dockerfile's output was retrieved.
    if (logs.trim()) info('buildLogs() returned a one-shot text response (state and any retained output)')
    else info('build log response was empty')

    section('create a second sandbox from the same Dockerfile')
    started = Date.now()
    const second = await daytona.createFromBuild(ImageBuilder.fromDockerfile(dockerfile), {
      name: `dynbuild-second-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    sandboxes.push(second)
    assertStarted(second)
    const secondSecs = (Date.now() - started) / 1000
    info(`second create took ${secondSecs.toFixed(1)}s`)

    const marker2 = await second.process.run('cat /marker')
    if (marker2.exitCode === 0 && marker2.output.includes(RUN)) {
      pass('the second sandbox contains the expected marker')
    } else {
      fail('the second sandbox does not contain the expected marker')
      ok = false
    }

    // Timing is reported, never asserted.
    info(`elapsed: first ${firstSecs.toFixed(1)}s vs second ${secondSecs.toFixed(1)}s`)

    section("read the second sandbox's build logs")
    const secondLogs = await second.buildLogs()
    info(secondLogs || 'build log response was empty')

    section("change the Dockerfile's output")
    const other = await daytona.createFromBuild(ImageBuilder.fromDockerfile(`FROM ${IMAGE}\nRUN echo other-${RUN} > /marker\n`), {
      name: `dynbuild-other-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    sandboxes.push(other)
    assertStarted(other)
    const otherMarker = (await other.process.run('cat /marker')).output.trim()
    info(`marker: ${otherMarker}`)
    if (otherMarker.startsWith('other-')) pass('the changed Dockerfile produced the expected file content')
    else {
      fail("the changed Dockerfile's output is missing")
      ok = false
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
