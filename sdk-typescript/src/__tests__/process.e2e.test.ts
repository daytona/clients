// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { Daytona } from '../Daytona'
import type { ProcessHandle } from '../ProcessHandle'
import { Sandbox } from '../Sandbox'

jest.setTimeout(240000)

const describeProcessE2E = process.env.DAYTONA_PROCESS_E2E === '1' ? describe : describe.skip

function frameLines(data: string | undefined): string[] {
  if (!data) {
    return []
  }
  return data
    .split('\n')
    .map((line) => line.trimEnd())
    .filter((line) => line !== '')
}

async function readStreamLines(
  handle: ProcessHandle,
  count: number,
  cursor?: string,
): Promise<{ lines: string[]; cursor?: string }> {
  const lines: string[] = []
  let lastCursor = cursor

  for await (const event of handle.streamLogs(cursor ? { cursor } : undefined)) {
    lastCursor = event.cursor
    if (event.type !== 'log') {
      continue
    }

    for (const line of frameLines(event.frame.data)) {
      lines.push(line)
      if (lines.length === count) {
        return { lines, cursor: lastCursor }
      }
    }
  }

  return { lines, cursor: lastCursor }
}

describeProcessE2E('TypeScript SDK Process E2E (opt-in)', () => {
  let daytona: Daytona
  let sandbox: Sandbox

  beforeAll(async () => {
    if (!process.env.DAYTONA_API_KEY) {
      throw new Error('DAYTONA_API_KEY environment variable is required for Process E2E tests')
    }

    daytona = new Daytona()
    sandbox = await daytona.create({
      name: `sdk-ts-process-${Date.now()}`,
      language: 'python',
      labels: { purpose: 'process-e2e' },
    })
  })

  afterAll(async () => {
    if (!sandbox) {
      return
    }

    try {
      await daytona.delete(sandbox)
    } finally {
      await daytona[Symbol.asyncDispose]()
    }
  })

  test('reconnects from a fresh client via connect(id) for full replay', async () => {
    const handle = await sandbox.process.start({
      shellCommand: 'i=1; while [ "$i" -le 20 ]; do printf "line %02d\\n" "$i"; i=$((i+1)); sleep 0.05; done',
    })

    const freshClient = new Daytona()
    try {
      const freshSandbox = await freshClient.get(sandbox.id)
      const adopted = await freshSandbox.process.connect(handle.id)
      await handle.wait({ timeoutMs: 20000 })

      const page = await adopted.logs()
      const lines = page.frames.flatMap((frame) => frameLines(frame.data))
      expect(lines[0]).toBe('line 01')
      expect(lines[19]).toBe('line 20')
    } finally {
      await freshClient[Symbol.asyncDispose]()
    }
  })

  test('resumes log streaming from the saved cursor without gaps or duplicates', async () => {
    const handle = await sandbox.process.start({
      shellCommand: 'i=1; while [ "$i" -le 20 ]; do printf "line %02d\\n" "$i"; i=$((i+1)); sleep 0.05; done',
    })

    const firstRead = await readStreamLines(handle, 10)
    const secondRead = await readStreamLines(handle, 10, firstRead.cursor)

    expect(firstRead.lines).toEqual([
      'line 01',
      'line 02',
      'line 03',
      'line 04',
      'line 05',
      'line 06',
      'line 07',
      'line 08',
      'line 09',
      'line 10',
    ])
    expect(secondRead.lines).toEqual([
      'line 11',
      'line 12',
      'line 13',
      'line 14',
      'line 15',
      'line 16',
      'line 17',
      'line 18',
      'line 19',
      'line 20',
    ])
  })

  test('lists a running process and adopts it from a fresh client', async () => {
    const handle = await sandbox.process.start({ shellCommand: 'sleep 30', name: `adopt-${Date.now()}` })
    const freshClient = new Daytona()

    try {
      const freshSandbox = await freshClient.get(sandbox.id)
      const running = await freshSandbox.process.list({ state: 'running' })
      expect(running.some((process) => process.id === handle.id)).toBe(true)

      const adopted = await freshSandbox.process.get(handle.id)
      expect(adopted.id).toBe(handle.id)
      await adopted.kill()
      await adopted.wait({ timeoutMs: 10000 })
    } finally {
      await freshClient[Symbol.asyncDispose]()
    }
  })

  test('captures TERM trap output and terminates with escalation configured', async () => {
    const handle = await sandbox.process.start({
      shellCommand: 'trap "echo caught; exit 0" TERM; while true; do sleep 1; done',
      name: `trap-${Date.now()}`,
    })

    await handle.kill({ escalateAfterMs: 2000 })
    const result = await handle.wait({ timeoutMs: 10000 })
    const page = await handle.logs()
    const logs = page.frames.flatMap((frame) => frameLines(frame.data)).join('\n')

    expect(logs).toContain('caught')
    expect(result.reason === 'exited' || result.reason === 'signaled').toBe(true)
  })

  test('writes stdin to cat and closes stdin cleanly', async () => {
    const handle = await sandbox.process.start({ argv: ['cat'], stdin: 'pipe' })

    await handle.stdin('hello process\n')
    await handle.stdinEof()

    const result = await handle.wait({ timeoutMs: 10000 })
    const page = await handle.logs()
    const logs = page.frames.flatMap((frame) => frameLines(frame.data)).join('\n')

    expect(result.exitCode).toBe(0)
    expect(logs).toContain('hello process')
  })
})
