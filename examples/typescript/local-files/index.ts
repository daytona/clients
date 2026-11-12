/*
 * local-files — copy local files into an image with ImageBuilder.
 *
 * The builder declares the image the way 0.x `Image` did: a base, a project
 * directory, a file, a working directory. Building it packs the Dockerfile and
 * those files into a build context, uploads it and boots a sandbox from the
 * result. The project directory's `.dockerignore` keeps a log file out of the
 * image. Local files need Node.js.
 *
 * The script writes its small project into a temporary directory, so it runs
 * from anywhere.
 */

import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { ImageBuilder } from '@daytona/sdk/v2'
import { assertStarted, client, section, info, pass, fail, IMAGE, RUN } from '../_common'

function writeProject(root: string): void {
  mkdirSync(join(root, 'app'))
  writeFileSync(join(root, 'app', 'main.sh'), `#!/bin/sh\necho "local-files-${RUN}"\n`)
  chmodSync(join(root, 'app', 'main.sh'), 0o755)
  writeFileSync(join(root, 'app', 'debug.log'), 'should not reach the image\n')
  writeFileSync(join(root, 'app', '.dockerignore'), '*.log\n')
  writeFileSync(join(root, 'settings.conf'), `run=${RUN}\n`)
}

async function main() {
  const daytona = client()
  let ok = true
  let sandbox: Awaited<ReturnType<typeof daytona.create>> | undefined
  const root = mkdtempSync(join(tmpdir(), 'local-files-'))
  try {
    writeProject(root)
    const builder = ImageBuilder.fromRegistry(IMAGE)
      .addLocalDir(join(root, 'app'), '/app')
      .addLocalFile(join(root, 'settings.conf'), '/etc/example/')
      .workdir('/app')
    info('Dockerfile:')
    for (const line of builder.toDockerfile().trimEnd().split('\n')) info(`  ${line}`)

    section('build the image with its local files and boot it')
    sandbox = await daytona.createFromBuild(builder, { name: `local-files-${RUN}`, buildTimeoutSeconds: 400, waitSeconds: 400 })
    assertStarted(sandbox)

    section('the copied files are in the sandbox')
    const run = await sandbox.process.run('./main.sh && cat /etc/example/settings.conf')
    for (const line of run.output.trim().split('\n')) info(line)
    if (run.exitCode === 0 && run.output.includes(`local-files-${RUN}`) && run.output.includes(`run=${RUN}`)) {
      pass('the directory and the file were copied')
    } else {
      fail(`the copied files are missing (exit=${run.exitCode})`)
      ok = false
    }

    section('the .dockerignore kept the log out')
    const ls = await sandbox.process.run('ls -a /app')
    info(ls.output.split(/\s+/).join(' ').trim())
    if (ls.output.includes('debug.log') || ls.output.includes('.dockerignore')) {
      fail('an ignored file reached the image')
      ok = false
    } else {
      pass('debug.log and .dockerignore are not in the image')
    }
  } finally {
    section('cleanup')
    if (sandbox !== undefined) {
      try {
        await daytona.delete(sandbox)
        info(`deleted ${sandbox.id}`)
      } catch (error) {
        info(`could not delete ${sandbox.id}: ${String(error)}`)
      }
    }
    rmSync(root, { recursive: true, force: true })
  }
  process.exit(ok ? 0 : 1)
}

main().catch((error) => {
  console.error('Error:', error)
  process.exit(1)
})
