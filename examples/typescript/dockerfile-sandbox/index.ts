/*
 * dockerfile-sandbox — the happy path: build a Dockerfile, get a working
 * sandbox, do real work in it.
 *
 * One call builds an inline Dockerfile and waits for a sandbox to boot. Check
 * the returned state before running commands: the readiness wait can expire.
 * The image build and sandbox readiness have independent time budgets.
 *
 * The Dockerfile bakes in a script, a data file and an environment variable,
 * and the sandbox then proves all three survived into the running sandbox —
 * which is how you know it booted YOUR image and not the stock base.
 *
 * Deliberately uses only shell builtins, so it works whatever `DAYTONA_IMAGE`
 * is set to (alpine and debian-slim differ on package managers, not on `sh`).
 *
 * Set `DAYTONA_KEEP=1` to leave the sandbox running at the end instead of
 * deleting it, so you can poke at it yourself.
 */

import { ImageBuilder } from '@daytona/sdk/v2'
import { assertStarted, client, section, info, pass, fail, IMAGE, RUN } from '../_common'

// Everything here is baked at BUILD time. If the sandbox can see it at RUN
// time, the image it booted is the one we just built.
//
// The ENV value has no spaces, so the example stays independent of how the
// image's environment is quoted.
const DOCKERFILE = `FROM ${IMAGE}
ENV GREETING=hello-from-the-image
RUN mkdir -p /app \\
 && printf '#!/bin/sh\\necho "$GREETING"\\necho "built-for: ${RUN}"\\n' > /app/greet.sh \\
 && chmod +x /app/greet.sh
RUN echo '{"build":"${RUN}","ok":true}' > /app/build.json
WORKDIR /app
`

async function main() {
  const daytona = client()
  let ok = true
  let sandbox: Awaited<ReturnType<typeof daytona.create>> | undefined
  const keep = process.env.DAYTONA_KEEP === '1'

  try {
    section('build the Dockerfile and boot it, in one call')
    sandbox = await daytona.createFromBuild(ImageBuilder.fromDockerfile(DOCKERFILE), {
      name: `dockerfile-sandbox-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    info(`sandbox ${sandbox.id}`)
    info(`state   ${sandbox.state}`)
    assertStarted(sandbox)
    pass('the sandbox is running')

    section('run the script the Dockerfile baked in')
    const greet = await sandbox.process.run('/app/greet.sh')
    for (const line of greet.output.trim().split('\n')) info(line)
    if (greet.exitCode === 0 && greet.output.includes(RUN)) {
      pass('the baked script ran, and it is OUR build')
    } else {
      fail(`the baked script did not run as expected (exit=${greet.exitCode})`)
      ok = false
    }

    section('the ENV baked by the Dockerfile is set in the sandbox')
    const env = await sandbox.process.run('printenv GREETING')
    info(env.output.trim())
    if (env.output.includes('hello-from-the-image')) pass('ENV survived into the running sandbox')
    else {
      fail("the Dockerfile's ENV is missing")
      ok = false
    }

    section('WORKDIR and the baked data file')
    const pwd = await sandbox.process.run('pwd && cat build.json')
    for (const line of pwd.output.trim().split('\n')) info(line)
    if (pwd.output.includes('/app') && pwd.output.includes(RUN)) {
      pass('WORKDIR and the baked file are both there')
    } else {
      fail('WORKDIR or the baked file is missing')
      ok = false
    }

    section('write and read a file at runtime')
    await sandbox.fs.writeText('/app/runtime.txt', 'written by the example\n')
    const back = await sandbox.fs.readText('/app/runtime.txt')
    info(back.trim())
    if (back.includes('written by the example')) pass('the filesystem round-trips')
    else {
      fail('the runtime file did not round-trip')
      ok = false
    }

    section('create a second sandbox from the same Dockerfile')
    const twin = await daytona.createFromBuild(ImageBuilder.fromDockerfile(DOCKERFILE), {
      name: `dockerfile-sandbox-twin-${RUN}`,
      buildTimeoutSeconds: 400,
      waitSeconds: 400,
    })
    try {
      info(`sandbox ${twin.id} is ${twin.state}`)
      assertStarted(twin)
      const twinGreet = await twin.process.run('/app/greet.sh')
      if (twinGreet.exitCode === 0 && twinGreet.output.includes(RUN)) {
        pass('the second sandbox runs the expected script')
      } else {
        fail('the second sandbox is not running the expected script')
        ok = false
      }
    } finally {
      await daytona.delete(twin)
      info(`deleted ${twin.id}`)
    }
  } finally {
    section('cleanup')
    if (sandbox && !keep) {
      try {
        await daytona.delete(sandbox)
        info(`deleted ${sandbox.id}`)
      } catch (error) {
        // Cleanup must not mask a real failure above.
        info(`could not delete ${sandbox.id}: ${error}`)
      }
    } else if (sandbox) {
      info(`DAYTONA_KEEP=1 — leaving ${sandbox.id} (${sandbox.state ?? 'unknown'}) in place`)
      info(`connect with: daytona.get('${sandbox.id}')`)
    }
  }

  process.exit(ok ? 0 : 1)
}

main().catch((error) => {
  console.error('Error:', error)
  process.exit(1)
})
