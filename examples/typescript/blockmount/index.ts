/**
 * BLOCKMOUNT shared performance volume — concurrent two-writer merge demo.
 *
 * Two sandboxes mount the SAME blockmount volume at the same time (blockmount
 * volumes allow concurrent multi-sandbox use):
 *
 *   sandbox A writes: fileA.txt, fileB.txt, fileC.txt
 *   sandbox B writes: fileB.txt, fileX.txt, fileZ.txt
 *
 * Each sandbox works on its own runner-local scratch at native disk speed; a
 * background loop commits changes to the shared S3 store (plus a final commit
 * when the sandbox is deleted). The store delta-merges both trees:
 *
 *   - disjoint files (fileA, fileC, fileX, fileZ) are all kept — the union
 *   - the overlapping fileB.txt is resolved per-file LAST-CHANGE-WINS by mtime
 *     (independent of commit order), and the conflict is recorded on the
 *     manifest with both content hashes — nothing is destroyed
 *
 * Running sandboxes do NOT see each other's writes automatically — each works
 * on its private scratch. `sandbox.pullVolumes()` is the explicit
 * synchronization point: it commits the sandbox's local changes and applies the
 * volume's latest merged state in place, WITHOUT stopping either sandbox.
 */
import { Daytona, Sandbox, VolumeType } from '@daytona/sdk'

// Poll until a deleted sandbox is fully destroyed on the runner. Deletion is async;
// the runner flushes its final blockmount commit as part of teardown, so "destroyed"
// is the barrier that guarantees this sandbox's writes are durable in the store.
async function waitUntilDeleted(sandbox: Sandbox, timeoutMs = 90_000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      await sandbox.refreshData()
    } catch {
      return // 404 — the record is gone, so it is fully destroyed
    }
    if (sandbox.state === 'destroyed') {
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for sandbox ${sandbox.id} to be destroyed`)
}

async function main() {
  // A blockmount volume's data lives in one region (chosen at creation, fixed for its lifetime).
  // Region is a performance/placement knob, NOT an attach restriction: sandboxes in any region can
  // mount the volume. We colocate the sandboxes with the volume's region below because that gives
  // the fastest commit/materialize (cross-region works but pays the distance to the store).
  // Discover a region a superadmin has enabled for blockmount.
  const bootstrap = new Daytona()
  const regions = await bootstrap.volume.listBlockmountRegions()
  if (regions.length === 0) {
    throw new Error('No region offers blockmount volumes. Ask a superadmin to enable one.')
  }
  const region = regions[0].id
  console.log(`Using blockmount region: ${regions[0].name} (${region})`)

  // Scope the client to that region so the sandboxes below land in it (the SDK takes the target
  // region from the client config), colocating them with the volume for best performance.
  const daytona = new Daytona({ target: region })

  // Create the shared performance volume in the chosen region.
  const volume = await daytona.volume.get('blockmount-merge-demo', true, {
    type: VolumeType.BLOCKMOUNT,
    region,
  })
  console.log(`Volume ${volume.name} (${volume.id}) type=${volume.type} state=${volume.state}`)

  const mountDir = '/home/daytona/shared'

  // Both sandboxes mount the same volume CONCURRENTLY — no exclusivity error; each gets its own
  // local scratch and the store reconciles them. They land in the volume's region via the client.
  const [sandboxA, sandboxB] = await Promise.all([
    daytona.create({ language: 'typescript', volumes: [{ volumeId: volume.id, mountPath: mountDir }] }),
    daytona.create({ language: 'typescript', volumes: [{ volumeId: volume.id, mountPath: mountDir }] }),
  ])
  console.log(`Sandbox A: ${sandboxA.id}`)
  console.log(`Sandbox B: ${sandboxB.id}`)

  // Sandbox A writes fileA, fileB, fileC.
  await sandboxA.fs.uploadFile(Buffer.from('from-A'), `${mountDir}/fileA.txt`)
  await sandboxA.fs.uploadFile(Buffer.from('fileB-written-by-A'), `${mountDir}/fileB.txt`)
  await sandboxA.fs.uploadFile(Buffer.from('from-A'), `${mountDir}/fileC.txt`)

  // Small gap so B's fileB.txt edit has a strictly newer mtime than A's (mtime
  // resolution is what decides the merge, not commit or pull order).
  await new Promise((resolve) => setTimeout(resolve, 1500))

  // Sandbox B writes fileB (the same path!), fileX, fileZ. B's fileB.txt edit
  // happens AFTER A's, so B's version has the newer mtime and will win the merge.
  await sandboxB.fs.uploadFile(Buffer.from('fileB-written-by-B-newer'), `${mountDir}/fileB.txt`)
  await sandboxB.fs.uploadFile(Buffer.from('from-B'), `${mountDir}/fileX.txt`)
  await sandboxB.fs.uploadFile(Buffer.from('from-B'), `${mountDir}/fileZ.txt`)

  // A and B do NOT see each other's writes yet — each works on its private scratch.
  console.log(
    'A sees (its own local scratch only):',
    (await sandboxA.fs.listFiles(mountDir)).map((f) => f.name),
  )
  console.log(
    'B sees (its own local scratch only):',
    (await sandboxB.fs.listFiles(mountDir)).map((f) => f.name),
  )

  // EXPLICIT PULL — no restart, both sandboxes keep running. Each pull first
  // commits the sandbox's local changes (so they join the merge), then applies
  // the latest merged state onto the live mount. Pulls only see what has been
  // committed at that moment, so full two-way convergence takes: A pull (commits
  // A's writes), B pull (commits B's writes + applies A's), A pull again
  // (applies B's).
  console.log('A pull #1:', await sandboxA.pullVolumes())
  console.log('B pull:   ', await sandboxB.pullVolumes())
  console.log('A pull #2:', await sandboxA.pullVolumes())

  // Both now see the union of both writers' files.
  console.log(
    'A sees after pull:',
    (await sandboxA.fs.listFiles(mountDir))
      .map((f) => f.name)
      .filter((n) => n !== 'lost+found')
      .sort(),
  )
  console.log(
    'B sees after pull:',
    (await sandboxB.fs.listFiles(mountDir))
      .map((f) => f.name)
      .filter((n) => n !== 'lost+found')
      .sort(),
  )
  // -> both: fileA.txt, fileB.txt, fileC.txt, fileX.txt, fileZ.txt

  // The overlapping fileB.txt converged to B's version (newer mtime) on BOTH sides:
  // A's copy was replaced by pull #2; B's copy was preserved (it was the winner).
  const fileBonA = await sandboxA.fs.downloadFile(`${mountDir}/fileB.txt`)
  const fileBonB = await sandboxB.fs.downloadFile(`${mountDir}/fileB.txt`)
  console.log(`fileB.txt on A: "${fileBonA.toString()}"`)
  console.log(`fileB.txt on B: "${fileBonB.toString()}"`)
  // -> both: "fileB-written-by-B-newer" — the newer mtime won, on both sandboxes

  // The reconciliation evidence is surfaced on the volume itself: the latest
  // manifest id and the conflicts the merge resolved (winner, reason, both shas).
  const refreshed = await daytona.volume.get(volume.name)
  console.log(`lastManifestId: ${refreshed.lastManifestId}`)
  for (const conflict of refreshed.conflicts ?? []) {
    console.log(
      `conflict: path=${conflict.path} winner=${conflict.winner} reason=${conflict.reason}` +
        ` oursSha=${conflict.oursSha?.slice(0, 12)} theirsSha=${conflict.theirsSha?.slice(0, 12)}`,
    )
  }
  // The losing version of fileB.txt is NOT destroyed: both content hashes remain
  // in the store's CAS and are recoverable from the previous manifest.

  // Cleanup. Wait for the sandboxes to be fully destroyed before deleting the
  // volume, otherwise the volume still counts as in use.
  await daytona.delete(sandboxA)
  await daytona.delete(sandboxB)
  await Promise.all([waitUntilDeleted(sandboxA), waitUntilDeleted(sandboxB)])
  await daytona.volume.delete(volume)
}

main()
