/*
 * lifecycle — create, get, list, run, and delete a sandbox.
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/lifecycle/index.ts
 */

import { client, section, info, pass, fail, RUN, sandboxImage } from '../_common'

async function main() {
  const daytona = client()

  section('create')
  const sandbox = await daytona.create(await sandboxImage(daytona), { name: `life-${RUN}`, waitSeconds: 200 })
  info(`created id=${sandbox.id} state=${sandbox.state} cpu=${sandbox.cpu} mem=${sandbox.memory}`)

  let ok = true
  try {
    section('get(id) + list')
    const got = await daytona.get(sandbox.id)
    info(`get(id) -> ${got.id}`)
    let found = false
    for await (const candidate of daytona.iter()) {
      if (candidate.id === sandbox.id) {
        found = true
        break
      }
    }
    info(`list finds it: ${found}`)

    section('run round-trip')
    const res = await sandbox.process.run('echo "Hello World from run!"')
    info(`exit=${res.exitCode} out=${res.output.trim()}`)
    if (res.exitCode === 0) pass('sandbox lifecycle round-trip completed')
    else {
      fail(`run failed (exit=${res.exitCode})`)
      ok = false
    }
  } finally {
    section('delete')
    await daytona.delete(sandbox)
    info(`deleted ${sandbox.id}`)
  }

  process.exit(ok ? 0 : 1)
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
