import assert from 'node:assert/strict'
import { Writable } from 'node:stream'
import { test } from 'node:test'
import { buildLogOutput } from './output.js'

test('the log callback waits for a slow writable instead of queueing the next chunk', async () => {
  const chunks = ['first build chunk\n', 'second build chunk\n']
  const received: Buffer[] = []
  const flushes: (() => void)[] = []
  const stream = new Writable({
    highWaterMark: 1,
    write(chunk: Buffer, _encoding, callback) {
      received.push(Buffer.from(chunk))
      flushes.push(callback)
    },
  })
  const controller = new AbortController()
  const output = buildLogOutput(stream, controller)
  const delivered: string[] = []
  // Match the SDK consumer: await each callback before offering the next chunk.
  const pending = (async () => {
    for (const chunk of chunks) {
      await output.write(chunk)
      delivered.push(chunk)
    }
  })()
  try {
    await new Promise<void>(resolve => setImmediate(resolve))
    assert.deepEqual(delivered, [])
    assert.deepEqual(received, [Buffer.from(chunks[0])])
    assert.equal(flushes.length, 1)
    assert.equal(stream.writableNeedDrain, true)
    assert.equal(stream.writableLength, Buffer.byteLength(chunks[0]))
    flushes[0]()

    await new Promise<void>(resolve => setImmediate(resolve))
    assert.deepEqual(received, chunks.map(chunk => Buffer.from(chunk)))
    assert.equal(flushes.length, 2)
    // The second write has reached the sink, but its promise is still pending.
    assert.deepEqual(delivered, [chunks[0]])
    assert.equal(stream.writableNeedDrain, true)
    assert.equal(stream.writableLength, Buffer.byteLength(chunks[1]))
    flushes[1]()

    await pending
    assert.deepEqual(delivered, chunks)
    assert.deepEqual(Buffer.concat(received), Buffer.from(chunks.join('')))
    assert.equal(stream.writableLength, 0)
    assert.equal(controller.signal.aborted, false)
  } finally {
    stream.destroy()
  }
})

test('EPIPE aborts observation and is handled both in the callback and error event', async () => {
  const pipeClosed = Object.assign(new Error('reader closed'), { code: 'EPIPE' })
  const stream = new Writable({ write(_chunk, _encoding, callback) { callback(pipeClosed) } })
  const controller = new AbortController()
  const output = buildLogOutput(stream, controller)
  await assert.rejects(output.write('build output'), error => error === pipeClosed)
  await new Promise<void>(resolve => setImmediate(resolve))
  assert.equal(output.error, pipeClosed)
  assert.equal(output.error?.code, 'EPIPE')
  assert.equal(controller.signal.aborted, true)
  assert.equal(stream.destroyed, true)
})
