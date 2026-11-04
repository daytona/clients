/*
 * exec-command — run shell commands and interpreter code.
 *
 * Run (from examples):
 *     DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/exec-command/index.ts
 */

import type { Sandbox } from '@daytona/sdk/v2'
import { client, section, info, IMAGE, RUN } from '../_common'

async function basicExec(sandbox: Sandbox) {
  section('basic run + interpreter')

  // Run a shell command.
  const cmd = await sandbox.process.run('echo "Hello World from CMD!"')
  if (cmd.exitCode !== 0) console.error('command failed:', cmd.exitCode)
  else info(`cmd: ${cmd.output.trim()}`)

  // Run code in the sandbox's configured language runtime (python here).
  const code = await sandbox.interpreter.run('print("Hello World from code!")', { language: 'python' })
  if (code.process.exitCode !== 0) console.error('code failed:', code.process.exitCode)
  else info(`code: ${code.logs.stdout.trim()}`)
}

async function main() {
  const daytona = client()

  const sandbox = await daytona.create(IMAGE, { name: `exec-${RUN}`, wait: 200 })
  info(`created sandbox id=${sandbox.id} state=${sandbox.state}`)

  try {
    await basicExec(sandbox)
  } finally {
    section('cleanup')
    await daytona.delete(sandbox)
    info(`deleted sandbox ${sandbox.id}`)
  }
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
