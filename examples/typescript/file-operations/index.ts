/*
 * file-operations — upload, list, search, replace, and download files.
 *
 * Uses the single-file helpers `fs.writeBytes(path, bytes)` and
 * `fs.writeText(path, text)` to write, and `fs.readBytes(path)` (returns a
 * Uint8Array) to read. Paths are relative to the sandbox's working directory.
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/file-operations/index.ts
 */

import { assertStarted, client, section, info, pass, fail, RUN, sandboxImage } from '../_common'

async function main() {
  const daytona = client()
  const sandbox = await daytona.create(await sandboxImage(daytona), { name: `files-${RUN}`, waitSeconds: 200 })
  info(`created sandbox id=${sandbox.id}`)

  let ok = true
  try {
    assertStarted(sandbox)
    const dir = '/tmp/project-files'

    section('mkdir + list')
    await sandbox.fs.mkdir(dir, { mode: '755' })
    const before = (await sandbox.fs.list('/tmp')).items
    info(`entries in /tmp: ${before.map((f) => f.name).join(', ')}`)

    section('writeBytes / writeText (from memory)')
    const config = JSON.stringify({ name: 'demo', version: '1.0.0', settings: { debug: true } }, null, 2)
    await sandbox.fs.writeBytes(`${dir}/config.json`, new TextEncoder().encode(config))
    await sandbox.fs.writeText(`${dir}/script.sh`, '#!/bin/bash\necho "Hello from script!"\n')
    info('uploaded config.json + script.sh')

    section('run: verify + run the uploaded script')
    info((await sandbox.process.run(`ls -la ${dir}`)).output.trim())
    await sandbox.process.run(`chmod +x ${dir}/script.sh`)
    const scriptOut = await sandbox.process.run(`./${dir}/script.sh`)
    info(`script output: ${scriptOut.output.trim()}`)
    if (scriptOut.exitCode === 0) pass('the uploaded script ran')
    else {
      fail(`the uploaded script failed (exit=${scriptOut.exitCode})`)
      ok = false
    }

    section('glob (by name pattern)')
    const found = await sandbox.fs.glob(dir, '*.json')
    info(`json files: ${JSON.stringify(found.items)}`)

    section('replace (edit config in place)')
    await sandbox.fs.replace([`${dir}/config.json`], '"debug": true', '"debug": false')

    section('readBytes (read bytes back)')
    const bytes = await sandbox.fs.readBytes(`${dir}/config.json`)
    const text = new TextDecoder().decode(bytes)
    info(`config.json now:\n${text}`)
    if (text.includes('"debug": false')) pass('replace round-tripped through readBytes')
    else {
      fail(`expected the edited value in the downloaded file (got: ${text})`)
      ok = false
    }
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
