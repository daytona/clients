/*
 * lifecycle — create, get, list, run, and delete a sandbox.
 *
 * Run (from examples):
 *     DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/lifecycle/index.ts
 */

import { client, section, info, pass, fail, IMAGE, RUN } from '../_common'

async function main() {
  const daytona = client()

  section('create')
  await waitForImageReady(daytona, IMAGE)
  const sandbox = await daytona.create(IMAGE, { name: `life-${RUN}`, waitSeconds: 200 })
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

async function waitForImageReady(daytona: ReturnType<typeof client>, image: string): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const entry = await findImage(daytona, image)
    if (!entry && isRegistryRef(image)) return
    if (!entry) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      continue
    }
    if (entry?.status === 'ready') return
    if (entry.status === 'failed') throw new Error(`image ${image} failed`)
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error(`image ${image} did not become ready`)
}

async function findImage(daytona: ReturnType<typeof client>, image: string) {
  let cursor: string | undefined
  for (let page = 0; page < 50; page += 1) {
    const result = await daytona.images.list({ cursor })
    const entry = result.items.find((item) => item.name === image)
    if (entry || !result.nextCursor || result.nextCursor === cursor) return entry
    cursor = result.nextCursor
  }
  return undefined
}

function isRegistryRef(image: string): boolean {
  return image.includes('/') || image.includes(':') || image.includes('@')
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
