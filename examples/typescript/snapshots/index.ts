/* images — build a reusable image, then boot a sandbox from it. */

import { ImageBuilder } from '@daytona/sdk/v2'
import { assertStarted, client, section, info, pass, fail, IMAGE, RUN } from '../_common'

async function waitForImageReady(daytona: ReturnType<typeof client>, imageName: string): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const entry = (await daytona.images.list()).items.find((image) => image.name === imageName)
    if (entry?.status === 'ready') return
    if (entry?.status === 'failed') throw new Error(`image ${imageName} failed`)
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error(`image ${imageName} did not become ready`)
}

async function main() {
  const daytona = client()
  const imageName = `example-img-${RUN}`
  let ok = true

  section('images.build')
  const dockerfile = `FROM ${IMAGE}\nRUN pip install --no-cache-dir requests\n`
  const image = await daytona.images.build(ImageBuilder.fromDockerfile(dockerfile), { name: imageName })
  info(`image status=${image.status} id=${image.id}`)
  if (image.id) pass('image build accepted')
  else {
    fail('image build was not accepted')
    ok = false
  }

  section('images.list')
  const page = await daytona.images.list()
  info(`contains ours=${page.items.some((image) => image.name === imageName)}`)
  await waitForImageReady(daytona, imageName)

  section('create a sandbox from the registered image')
  const sandbox = await daytona.create(imageName, { name: `from-img-${RUN}`, waitSeconds: 200 })
  try {
    assertStarted(sandbox)
    const py = await sandbox.process.run('python --version')
    info(`sandbox python: ${py.output.trim()}`)
    if (py.exitCode === 0) pass('sandbox booted from the image and runs')
    else {
      fail(`sandbox from image failed to run (exit=${py.exitCode})`)
      ok = false
    }
  } finally {
    await daytona.delete(sandbox)
    info(`deleted sandbox ${sandbox.id}`)
  }

  process.exit(ok ? 0 : 1)
}

main().catch((error) => {
  console.error('Error:', error)
  process.exit(1)
})
