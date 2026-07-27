import { Daytona } from '@daytona/sdk'

async function main() {
  const daytona = new Daytona()

  // Create a warm pool that keeps ready-to-use sandboxes for an existing snapshot.
  // `target` is optional and defaults to the organization default region.
  const pool = await daytona.warmPool.create({ snapshot: 'my-snapshot', pool: 3 })
  console.log(`Created warm pool ${pool.id} for snapshot '${pool.snapshot}' in ${pool.target}`)

  // List warm pools. currentSize vs pool is the status check: currentSize is the
  // number of ready sandboxes; errorReason is set when the pool cannot be filled.
  const pools = await daytona.warmPool.list()
  for (const p of pools) {
    const status = p.errorReason ? ` (error: ${p.errorReason})` : ''
    console.log(`${p.snapshot} (${p.target}): ${p.currentSize}/${p.pool} ready${status}`)
  }

  // Grow the pool. Setting pool to 0 drains it without deleting the pool.
  const updated = await daytona.warmPool.update(pool.id, { pool: 5 })
  console.log(`Updated desired size to ${updated.pool}`)

  // Cleanup
  await daytona.warmPool.delete(pool.id)
  console.log('Deleted warm pool')
}

main()
