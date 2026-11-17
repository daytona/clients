// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { io } from 'socket.io-client'
import { EventDispatcher } from '../utils/EventDispatcher'
import { EventSubscriptionManager } from '../utils/EventSubscriptionManager'

jest.mock('socket.io-client', () => ({ io: jest.fn() }))

type Listener = (...args: unknown[]) => void

class FakeEmitter {
  private readonly handlers = new Map<string, Listener[]>()

  on(event: string, handler: Listener): this {
    const list = this.handlers.get(event) ?? []
    list.push(handler)
    this.handlers.set(event, list)
    return this
  }

  fire(event: string, ...args: unknown[]): void {
    for (const handler of this.handlers.get(event) ?? []) {
      handler(...args)
    }
  }

  removeAllListeners(): this {
    this.handlers.clear()
    return this
  }
}

class FakeSocket extends FakeEmitter {
  connected = false
  auth: unknown
  readonly io = new FakeEmitter()
  readonly emit = jest.fn()
  readonly connect = jest.fn(() => this)
  readonly disconnect = jest.fn(() => {
    this.connected = false
    return this
  })

  serverAccepts(): void {
    this.connected = true
    this.fire('connect')
  }

  drop(): void {
    this.connected = false
    this.fire('disconnect', 'transport close')
  }
}

const ioMock = io as unknown as jest.Mock
const SANDBOX_EVENTS = ['sandbox.state.updated']

const uuid = (n: number): string => `00000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`

describe('EventDispatcher', () => {
  let sockets: FakeSocket[]
  let dispatchers: EventDispatcher[]

  const makeDispatcher = (): EventDispatcher => {
    const dispatcher = new EventDispatcher('https://api.example.test/api', 'token', 'org-1', 'sdk-typescript', '1.2.3')
    dispatchers.push(dispatcher)
    return dispatcher
  }

  const lastSocket = (): FakeSocket => {
    const socket = sockets[sockets.length - 1]
    if (!socket) throw new Error('no socket was created')
    return socket
  }

  const noop = (): void => undefined

  beforeEach(() => {
    jest.useFakeTimers()
    sockets = []
    dispatchers = []
    ioMock.mockReset()
    ioMock.mockImplementation(() => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    })
  })

  afterEach(() => {
    for (const dispatcher of dispatchers) dispatcher.disconnect()
    jest.useRealTimers()
  })

  it('does not open a connection until the first subscription', () => {
    const dispatcher = makeDispatcher()
    const manager = new EventSubscriptionManager(dispatcher)
    expect(ioMock).not.toHaveBeenCalled()

    manager.subscribe(uuid(1), noop, SANDBOX_EVENTS)

    expect(ioMock).toHaveBeenCalledTimes(1)
    expect(lastSocket().connect).toHaveBeenCalledTimes(1)
  })

  it('opts in to explicit subscription mode in the handshake query', () => {
    const dispatcher = makeDispatcher()
    dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)

    const options = ioMock.mock.calls[0][1] as { query: Record<string, string>; path: string }
    expect(options.path).toBe('/api/socket.io/')
    expect(options.query).toEqual({
      organizationId: 'org-1',
      source: 'sdk-typescript',
      sdkVersion: '1.2.3',
      subscriptionMode: 'explicit',
    })
  })

  it('sends nothing while disconnected and the whole current set on connect', () => {
    const dispatcher = makeDispatcher()
    const unsubscribeA = dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)
    dispatcher.subscribe(uuid(2), noop, SANDBOX_EVENTS)
    unsubscribeA()
    dispatcher.subscribe(uuid(3), noop, SANDBOX_EVENTS)
    const socket = lastSocket()
    expect(socket.emit).not.toHaveBeenCalled()

    socket.serverAccepts()

    expect(socket.emit).toHaveBeenCalledTimes(1)
    expect(socket.emit).toHaveBeenCalledWith('subscribe', { sandboxIds: [uuid(2), uuid(3)] })
  })

  it('chunks the set sent on connect to at most 100 ids per message', () => {
    const dispatcher = makeDispatcher()
    const ids = Array.from({ length: 250 }, (_, i) => uuid(i))
    for (const id of ids) dispatcher.subscribe(id, noop, SANDBOX_EVENTS)
    const socket = lastSocket()

    socket.serverAccepts()

    expect(socket.emit.mock.calls).toEqual([
      ['subscribe', { sandboxIds: ids.slice(0, 100) }],
      ['subscribe', { sandboxIds: ids.slice(100, 200) }],
      ['subscribe', { sandboxIds: ids.slice(200) }],
    ])
  })

  it('re-sends the whole set after a reconnect', () => {
    const dispatcher = makeDispatcher()
    dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)
    dispatcher.subscribe(uuid(2), noop, SANDBOX_EVENTS)
    const socket = lastSocket()
    socket.serverAccepts()
    socket.emit.mockClear()

    socket.drop()
    dispatcher.subscribe(uuid(3), noop, SANDBOX_EVENTS)
    expect(socket.emit).not.toHaveBeenCalled()
    expect(dispatcher.isConnected).toBe(false)

    socket.serverAccepts()

    expect(dispatcher.isConnected).toBe(true)
    expect(socket.emit.mock.calls).toEqual([['subscribe', { sandboxIds: [uuid(1), uuid(2), uuid(3)] }]])
  })

  it('advances the connection epoch on the initial connect and on every reconnect', () => {
    const dispatcher = makeDispatcher()
    const manager = new EventSubscriptionManager(dispatcher)
    manager.subscribe(uuid(1), noop, SANDBOX_EVENTS)
    const socket = lastSocket()
    expect(manager.connectionEpoch).toBe(0)

    socket.serverAccepts()
    const afterConnect = manager.connectionEpoch
    expect(afterConnect).toBeGreaterThan(0)

    socket.drop()
    expect(manager.connectionEpoch).toBe(afterConnect)
    socket.io.fire('reconnect')
    socket.serverAccepts()

    expect(manager.connectionEpoch).toBeGreaterThan(afterConnect)
    expect(new EventSubscriptionManager(null).connectionEpoch).toBe(0)
  })

  describe('per-connection subscription cap', () => {
    const makeCapped = (): EventDispatcher => {
      const dispatcher = new EventDispatcher(
        'https://api.example.test/api',
        'token',
        undefined,
        undefined,
        undefined,
        2,
      )
      dispatchers.push(dispatcher)
      return dispatcher
    }

    it('serves ids up to the cap and sends nothing for the rest', () => {
      const dispatcher = makeCapped()
      const manager = new EventSubscriptionManager(dispatcher)
      manager.subscribe(uuid(1), noop, SANDBOX_EVENTS)
      lastSocket().serverAccepts()
      manager.subscribe(uuid(2), noop, SANDBOX_EVENTS)
      manager.subscribe(uuid(3), noop, SANDBOX_EVENTS)

      expect(lastSocket().emit.mock.calls).toEqual([
        ['subscribe', { sandboxIds: [uuid(1)] }],
        ['subscribe', { sandboxIds: [uuid(2)] }],
      ])
      expect(dispatcher.isServed(uuid(3))).toBe(false)
      expect(manager.isEventBacked(uuid(1))).toBe(true)
      expect(manager.isEventBacked(uuid(3))).toBe(false)
    })

    it('frees a slot on the last listener, does not promote an unserved id, and serves the next new one', () => {
      const dispatcher = makeCapped()
      const releaseFirst = dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)
      dispatcher.subscribe(uuid(2), noop, SANDBOX_EVENTS)
      const releaseThird = dispatcher.subscribe(uuid(3), noop, SANDBOX_EVENTS)
      const socket = lastSocket()
      socket.serverAccepts()
      socket.emit.mockClear()

      releaseFirst()
      expect(socket.emit.mock.calls).toEqual([['unsubscribe', { sandboxIds: [uuid(1)] }]])
      expect(dispatcher.isServed(uuid(3))).toBe(false)

      dispatcher.subscribe(uuid(4), noop, SANDBOX_EVENTS)
      expect(socket.emit.mock.calls[1]).toEqual(['subscribe', { sandboxIds: [uuid(4)] }])

      releaseThird()
      expect(socket.emit).toHaveBeenCalledTimes(2)
    })

    it('re-sends exactly the served ids on reconnect', () => {
      const dispatcher = makeCapped()
      dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)
      dispatcher.subscribe(uuid(2), noop, SANDBOX_EVENTS)
      dispatcher.subscribe(uuid(3), noop, SANDBOX_EVENTS)
      const socket = lastSocket()
      socket.serverAccepts()
      expect(socket.emit.mock.calls).toEqual([['subscribe', { sandboxIds: [uuid(1), uuid(2)] }]])
      socket.emit.mockClear()

      socket.drop()
      socket.serverAccepts()

      expect(socket.emit.mock.calls).toEqual([['subscribe', { sandboxIds: [uuid(1), uuid(2)] }]])
    })

    it('is not event-backed while disconnected or after the manager shuts down', () => {
      const dispatcher = makeCapped()
      const manager = new EventSubscriptionManager(dispatcher)
      manager.subscribe(uuid(1), noop, SANDBOX_EVENTS)
      expect(manager.isEventBacked(uuid(1))).toBe(false)

      lastSocket().serverAccepts()
      expect(manager.isEventBacked(uuid(1))).toBe(true)

      manager.shutdown()
      expect(manager.isEventBacked(uuid(1))).toBe(false)
      expect(new EventSubscriptionManager(null).isEventBacked(uuid(1))).toBe(false)
    })
  })

  it('subscribes on the first listener and unsubscribes on the last while connected', () => {
    const dispatcher = makeDispatcher()
    const unsubscribeFirst = dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)
    const socket = lastSocket()
    socket.serverAccepts()
    socket.emit.mockClear()

    const unsubscribeSecond = dispatcher.subscribe(uuid(1), () => undefined, SANDBOX_EVENTS)
    expect(socket.emit).not.toHaveBeenCalled()

    const unsubscribeOther = dispatcher.subscribe(uuid(2), noop, SANDBOX_EVENTS)
    expect(socket.emit.mock.calls).toEqual([['subscribe', { sandboxIds: [uuid(2)] }]])
    socket.emit.mockClear()

    unsubscribeFirst()
    expect(socket.emit).not.toHaveBeenCalled()

    unsubscribeSecond()
    expect(socket.emit.mock.calls).toEqual([['unsubscribe', { sandboxIds: [uuid(1)] }]])
    socket.emit.mockClear()

    // Calling an unsubscribe function again must not send anything.
    unsubscribeSecond()
    expect(socket.emit).not.toHaveBeenCalled()

    unsubscribeOther()
    expect(socket.emit.mock.calls).toEqual([['unsubscribe', { sandboxIds: [uuid(2)] }]])
  })

  it('does not raise into the caller when an emit fails', () => {
    const dispatcher = makeDispatcher()
    dispatcher.subscribe(uuid(1), noop, SANDBOX_EVENTS)
    const socket = lastSocket()
    socket.emit.mockImplementation(() => {
      throw new Error('boom')
    })

    expect(() => socket.serverAccepts()).not.toThrow()
    expect(() => dispatcher.subscribe(uuid(2), noop, SANDBOX_EVENTS)).not.toThrow()
  })

  it('delivers slim state payloads only to listeners of the matching sandbox', () => {
    const dispatcher = makeDispatcher()
    const handlerA = jest.fn()
    const handlerB = jest.fn()
    dispatcher.subscribe(uuid(1), handlerA, SANDBOX_EVENTS)
    dispatcher.subscribe(uuid(2), handlerB, SANDBOX_EVENTS)
    const socket = lastSocket()
    socket.serverAccepts()

    const payload = { sandbox: { id: uuid(1), state: 'started' }, oldState: 'starting', newState: 'started' }
    socket.fire('sandbox.state.updated', payload)
    socket.fire('sandbox.state.updated', { sandbox: { id: uuid(9), state: 'started' } })

    expect(handlerA).toHaveBeenCalledWith('sandbox.state.updated', payload)
    expect(handlerA).toHaveBeenCalledTimes(1)
    expect(handlerB).not.toHaveBeenCalled()
  })
})
