/*
 * git — clone a repo, stage a change, commit, branch, and read status.
 *
 * Shows clone / add / commit / branches / createBranch / checkoutBranch /
 * deleteBranch / status / push / pull.
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/git/index.ts
 */

import { assertStarted, client, section, info, pass, fail, RUN, sandboxImage } from '../_common'

// A small, stable public repo to clone shallowly.
const REPO_URL = 'https://github.com/octocat/Hello-World'

async function main(): Promise<boolean> {
  const daytona = client()
  const sandbox = await daytona.create(await sandboxImage(daytona), { name: `git-${RUN}`, waitSeconds: 200 })
  info(`created sandbox id=${sandbox.id}`)

  let ok = true
  try {
    assertStarted(sandbox)
    const git = sandbox.git
    const proc = sandbox.process
    const repo = '/tmp/demo-repo'

    // git needs to be installed in the image; python:3.x-slim ships without it.
    section('ensure git is available')
    const has = await proc.run('command -v git || (apt-get update && apt-get install -y git)')
    if (has.exitCode !== 0) {
      fail(`could not ensure git: ${has.output.trim()}`)
      return false
    }
    info((await proc.run('git --version')).output.trim())

    section('clone')
    await git.clone(REPO_URL, { path: repo, branch: 'master' })
    info(`cloned ${REPO_URL} -> ${repo}`)

    section('status (freshly cloned -> clean)')
    const st0 = await git.status(repo)
    info(`current branch=${st0.currentBranch}`)

    section('add + commit a new file')
    await sandbox.fs.writeText(`${repo}/daytona-note.txt`, `hello from ${RUN}\n`)
    await git.add(repo, ['daytona-note.txt'])
    const commit = await git.commit(repo, 'add daytona-note', { author: 'Ada Lovelace', email: 'ada@example.com' })
    info(`commit hash=${commit.hash}`)

    section('branches + createBranch + checkout + delete')
    await git.createBranch(repo, 'feature')
    await git.checkoutBranch(repo, 'feature')
    const branches = await git.listBranches(repo)
    info(`branches: ${JSON.stringify(branches.items)}`)
    await git.checkoutBranch(repo, 'master')
    await git.deleteBranch(repo, 'feature')
    info('created, checked out, then deleted branch "feature"')

    section('verify the commit landed')
    const log = await proc.run(`git -C ${repo} log --oneline -1`)
    info(`HEAD: ${log.output.trim()}`)
    if (log.output.includes('add daytona-note')) pass('commit is visible in the log')
    else {
      fail(`expected our commit at HEAD (got: ${log.output.trim()})`)
      ok = false
    }
  } finally {
    section('cleanup')
    await daytona.delete(sandbox)
    info(`deleted sandbox ${sandbox.id}`)
  }

  return ok
}

main().then((ok) => process.exit(ok ? 0 : 1)).catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
