/*
 * pagination — list images with iter() or page by page with pagination cursors.
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... npx tsx typescript/pagination/index.ts
 */

import { client, section, info } from '../_common'

async function main() {
  const daytona = client()

  section('images.iter — lazy all pages')
  for await (const image of daytona.images.iter({ limit: 10 })) {
    info(`   - ${image.name} (${image.status ?? 'unknown'})`)
  }

  section('images.list — walk pages until exhausted')
  const limit = 10
  let page = 1
  let seen = 0
  let cursor: string | undefined
  do {
    const result = await daytona.images.list({ cursor, limit })
    seen += result.items.length
    info(`page ${page}: ${result.items.length} item(s)`)
    for (const image of result.items) info(`   - ${image.name} (${image.status ?? 'unknown'})`)
    cursor = result.nextCursor
    if (!cursor || result.items.length === 0) break
    page += 1
  } while (true)

  info(`walked ${seen} image(s)`)
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
