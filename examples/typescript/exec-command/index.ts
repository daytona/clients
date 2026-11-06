/*
 * exec-command — run shell commands and interpreter code.
 *
 * Run (from examples):
 *     DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/exec-command/index.ts
 */

import type { Sandbox } from '@daytona/sdk/v2'
import { client, section, info, fail, RUN, sandboxImage } from '../_common'

/** Run a command and interpreter code; resolve to whether both succeeded. */
async function basicExec(sandbox: Sandbox): Promise<boolean> {
  section('basic run + interpreter')
  let ok = true

  // Run a shell command.
  const cmd = await sandbox.process.run('echo "Hello World from CMD!"')
  if (cmd.exitCode !== 0) {
    fail(`command failed (exit=${cmd.exitCode})`)
    ok = false
  } else info(`cmd: ${cmd.output.trim()}`)

  // Run code in the sandbox's configured language runtime (python here).
  const code = await sandbox.interpreter.run('print("Hello World from code!")', { language: 'python' })
  if (code.process.exitCode !== 0 || code.error) {
    fail(`code failed (exit=${code.process.exitCode}, error=${code.error?.name ?? 'none'})`)
    ok = false
  } else info(`code: ${code.logs.stdout.trim()}`)

  return ok
}

async function main() {
  const daytona = client()

  const sandbox = await daytona.create(await sandboxImage(daytona), { name: `exec-${RUN}`, waitSeconds: 200 })
  info(`created sandbox id=${sandbox.id} state=${sandbox.state}`)

  let ok = false
  try {
    ok = await basicExec(sandbox)
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
