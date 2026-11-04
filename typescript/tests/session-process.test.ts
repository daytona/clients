/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { HttpTransport } from '../src/v2/http'
import { ProcessService, UnsupportedOperationError } from '../src/v2/index'
import { PROCESS_KINDS } from '../src/v2/types'

describe('session process controls', () => {
  afterEach(() => jest.restoreAllMocks())

  it('recognizes sessions when listing shell processes', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const get = jest.spyOn(transport, 'get').mockResolvedValue({ items: [{ id: 'shell-1', kind: 'session', state: 'running' }] })
    // When
    const page = await new ProcessService(transport, 'sandbox-1').list({ kind: 'session' })
    // Then
    expect(get).toHaveBeenCalledWith('/processes?kind=session')
    expect(page.items[0]).toMatchObject({ kind: 'session', rawKind: 'session' })
    expect(PROCESS_KINDS).toContain('session')
  })

  it.each(['attach', 'resize', 'stdin', 'stdinEof'] as const)('refuses %s on a reconnected session process handle', async (verb) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const request = jest.spyOn(transport.http, 'request')
    const handle = new ProcessService(transport, 'sandbox-1').connect({ sandboxId: 'sandbox-1', processId: 'shell-1', kind: 'session' })
    const invoke = {
      attach: () => handle.attach(), resize: () => handle.resize(80, 24),
      stdin: () => handle.stdin('hello'), stdinEof: () => handle.stdinEof(),
    }
    // When / Then
    await expect(invoke[verb]()).rejects.toBeInstanceOf(UnsupportedOperationError)
    expect(request).not.toHaveBeenCalled()
  })

  it('deletes an encoded process id when using handle.delete', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const remove = jest.spyOn(transport, 'delete').mockResolvedValue(undefined)
    const handle = new ProcessService(transport, 'sandbox-1').connect('command/!')
    // When
    await handle.delete()
    // Then
    expect(remove).toHaveBeenCalledWith('/processes/command%2F%21')
  })

  it('signals only the command when interrupting a session command handle', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ id: 'command-1', kind: 'exec' })
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    const handle = await session.start('sleep 60')
    post.mockClear()
    // When
    await handle.kill({ signal: 'INT' })
    // Then
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/processes/command-1/signals', { signal: 'SIGINT', escalateAfterMs: undefined, escalateTo: undefined })
  })
})
