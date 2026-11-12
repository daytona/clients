// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { createApiResponse } from './helpers'
import { ComputerUse } from '../ComputerUse'

const mockDynamicImport = jest.fn()

jest.mock('@daytona/toolbox-api-client', () => ({}), { virtual: true })
jest.mock('../utils/Import', () => ({
  dynamicImport: (...args: unknown[]) => mockDynamicImport(...args),
}))

describe('ComputerUse', () => {
  const apiClient = {
    getMousePosition: jest.fn(),
    moveMouse: jest.fn(),
    mouseDown: jest.fn(),
    mouseUp: jest.fn(),
    click: jest.fn(),
    drag: jest.fn(),
    scroll: jest.fn(),
    typeText: jest.fn(),
    pressKey: jest.fn(),
    pressHotkey: jest.fn(),
    keyDown: jest.fn(),
    keyUp: jest.fn(),
    takeScreenshot: jest.fn(),
    takeRegionScreenshot: jest.fn(),
    takeCompressedScreenshot: jest.fn(),
    takeCompressedRegionScreenshot: jest.fn(),
    getDisplayInfo: jest.fn(),
    getWindows: jest.fn(),
    startRecording: jest.fn(),
    stopRecording: jest.fn(),
    listRecordings: jest.fn(),
    getRecording: jest.fn(),
    deleteRecording: jest.fn(),
    downloadRecording: jest.fn(),
    startComputerUse: jest.fn(),
    stopComputerUse: jest.fn(),
    getComputerUseStatus: jest.fn(),
    getProcessStatus: jest.fn(),
    restartProcess: jest.fn(),
    getProcessLogs: jest.fn(),
    getProcessErrors: jest.fn(),
    getAccessibilityTree: jest.fn(),
    findAccessibilityNodes: jest.fn(),
    focusAccessibilityNode: jest.fn(),
    invokeAccessibilityNode: jest.fn(),
    setAccessibilityNodeValue: jest.fn(),
  }

  const computerUse = new ComputerUse(apiClient as unknown as never)

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('delegates mouse/keyboard/screenshot/display operations', async () => {
    apiClient.getMousePosition.mockResolvedValue(createApiResponse({ x: 1, y: 2 }))
    apiClient.moveMouse.mockResolvedValue(createApiResponse({ x: 10, y: 20 }))
    apiClient.click.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.drag.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.scroll.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.takeScreenshot.mockResolvedValue(createApiResponse({ data: 'a' }))
    apiClient.takeRegionScreenshot.mockResolvedValue(createApiResponse({ data: 'b' }))
    apiClient.takeCompressedScreenshot.mockResolvedValue(createApiResponse({ data: 'c' }))
    apiClient.takeCompressedRegionScreenshot.mockResolvedValue(createApiResponse({ data: 'd' }))
    apiClient.getDisplayInfo.mockResolvedValue(createApiResponse({ total_displays: 1 }))
    apiClient.getWindows.mockResolvedValue(createApiResponse({ count: 0, windows: [] }))

    await expect(computerUse.mouse.getPosition()).resolves.toEqual({ x: 1, y: 2 })
    await expect(computerUse.mouse.move(10, 20)).resolves.toEqual({ x: 10, y: 20 })
    await expect(computerUse.mouse.click(10, 20)).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.drag(1, 2, 3, 4)).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.scroll(1, 2, 'down')).resolves.toBe(true)

    await computerUse.keyboard.type('hello', 1)
    await computerUse.keyboard.press('Enter', ['ctrl'])
    await computerUse.keyboard.hotkey('ctrl+c')

    await expect(computerUse.screenshot.takeFullScreen()).resolves.toEqual({ data: 'a' })
    await expect(computerUse.screenshot.takeRegion({ x: 1, y: 2, width: 3, height: 4 })).resolves.toEqual({ data: 'b' })
    await expect(computerUse.screenshot.takeCompressed()).resolves.toEqual({ data: 'c' })
    await expect(
      computerUse.screenshot.takeCompressedRegion({ x: 1, y: 2, width: 3, height: 4 }, { format: 'png' }),
    ).resolves.toEqual({ data: 'd' })

    await expect(computerUse.display.getInfo()).resolves.toEqual({ total_displays: 1 })
    await expect(computerUse.display.getWindows()).resolves.toEqual({ count: 0, windows: [] })
  })

  it('sends default mouse and keyboard payloads', async () => {
    apiClient.moveMouse.mockResolvedValue(createApiResponse({ x: 5, y: 6 }))
    apiClient.click.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.drag.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.scroll.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.typeText.mockResolvedValue(createApiResponse(undefined))
    apiClient.pressKey.mockResolvedValue(createApiResponse(undefined))
    apiClient.pressHotkey.mockResolvedValue(createApiResponse(undefined))

    await computerUse.mouse.move(5, 6)
    await computerUse.mouse.click(7, 8)
    await computerUse.mouse.drag(1, 2, 3, 4)
    await computerUse.mouse.scroll(9, 10, 'up')
    await computerUse.keyboard.type('hello')
    await computerUse.keyboard.press('Enter')
    await computerUse.keyboard.hotkey('ctrl+c')

    expect(apiClient.moveMouse).toHaveBeenCalledWith({ x: 5, y: 6 })
    expect(apiClient.click).toHaveBeenCalledWith({ x: 7, y: 8, button: 'left', double: false })
    expect(apiClient.drag).toHaveBeenCalledWith({ startX: 1, startY: 2, endX: 3, endY: 4, button: 'left' })
    expect(apiClient.scroll).toHaveBeenCalledWith({ x: 9, y: 10, direction: 'up', amount: 1 })
    expect(apiClient.typeText).toHaveBeenCalledWith({ text: 'hello', delay: undefined })
    expect(apiClient.pressKey).toHaveBeenCalledWith({ key: 'Enter', modifiers: [] })
    expect(apiClient.pressHotkey).toHaveBeenCalledWith({ keys: 'ctrl+c' })
  })

  it('delegates mouse and keyboard hold operations', async () => {
    apiClient.mouseDown.mockResolvedValue(createApiResponse({ x: 11, y: 22 }))
    apiClient.mouseUp.mockResolvedValue(createApiResponse({ x: 33, y: 44 }))
    apiClient.keyDown.mockResolvedValue(createApiResponse(undefined))
    apiClient.keyUp.mockResolvedValue(createApiResponse(undefined))

    await expect(computerUse.mouse.down(11, 22)).resolves.toEqual({ x: 11, y: 22 })
    await expect(computerUse.mouse.up(33, 44, 'right')).resolves.toEqual({ x: 33, y: 44 })
    await computerUse.keyboard.down('shift')
    await computerUse.keyboard.up('shift')

    expect(apiClient.mouseDown).toHaveBeenCalledWith({ x: 11, y: 22, button: 'left' })
    expect(apiClient.mouseUp).toHaveBeenCalledWith({ x: 33, y: 44, button: 'right' })
    expect(apiClient.keyDown).toHaveBeenCalledWith({ key: 'shift' })
    expect(apiClient.keyUp).toHaveBeenCalledWith({ key: 'shift' })
  })

  it('keeps both zero coordinates in mouse hold payloads', async () => {
    apiClient.mouseDown.mockResolvedValue(createApiResponse({ x: 0, y: 0 }))
    apiClient.mouseUp.mockResolvedValue(createApiResponse({ x: 0, y: 0 }))

    await expect(computerUse.mouse.down(0, 0)).resolves.toEqual({ x: 0, y: 0 })
    await expect(computerUse.mouse.up(0, 0)).resolves.toEqual({ x: 0, y: 0 })

    expect(apiClient.mouseDown).toHaveBeenCalledWith({ x: 0, y: 0, button: 'left' })
    expect(apiClient.mouseUp).toHaveBeenCalledWith({ x: 0, y: 0, button: 'left' })

    // Zeros must survive wire serialization instead of being dropped as falsy values.
    expect(JSON.parse(JSON.stringify(apiClient.mouseDown.mock.calls[0][0]))).toEqual({ x: 0, y: 0, button: 'left' })
    expect(JSON.parse(JSON.stringify(apiClient.mouseUp.mock.calls[0][0]))).toEqual({ x: 0, y: 0, button: 'left' })
  })

  it('omits mouse hold coordinates when they are not supplied', async () => {
    apiClient.mouseDown.mockResolvedValue(createApiResponse({ x: 7, y: 8 }))
    apiClient.mouseUp.mockResolvedValue(createApiResponse({ x: 7, y: 8 }))

    // x and y are optional so the press lands at the current cursor position,
    // matching the Python, Go, Ruby, and Java facades.
    await expect(computerUse.mouse.down()).resolves.toEqual({ x: 7, y: 8 })
    await expect(computerUse.mouse.up()).resolves.toEqual({ x: 7, y: 8 })

    // Use Object.keys rather than toEqual: toEqual ignores undefined-valued
    // properties and would pass even if x/y were serialized as null.
    const downPayload = JSON.parse(JSON.stringify(apiClient.mouseDown.mock.calls[0][0]))
    const upPayload = JSON.parse(JSON.stringify(apiClient.mouseUp.mock.calls[0][0]))
    expect(Object.keys(downPayload).sort()).toEqual(['button'])
    expect(Object.keys(upPayload).sort()).toEqual(['button'])
  })

  it('passes click counts and modifiers through to the api', async () => {
    apiClient.click.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.drag.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.scroll.mockResolvedValue(createApiResponse({ success: true }))

    await computerUse.mouse.click(10, 20, 'left', false, 3, ['ctrl', 'shift'])
    await computerUse.mouse.drag(1, 2, 3, 4, 'left', ['alt'])
    await computerUse.mouse.scroll(5, 6, 'left', 2, ['cmd'])
    await computerUse.mouse.scroll(7, 8, 'right', 3)

    expect(apiClient.click).toHaveBeenCalledWith({
      x: 10,
      y: 20,
      button: 'left',
      double: false,
      clicks: 3,
      modifiers: ['ctrl', 'shift'],
    })
    expect(JSON.parse(JSON.stringify(apiClient.click.mock.calls[0][0])).clicks).toBe(3)
    expect(apiClient.drag).toHaveBeenCalledWith({
      startX: 1,
      startY: 2,
      endX: 3,
      endY: 4,
      button: 'left',
      modifiers: ['alt'],
    })
    expect(apiClient.scroll).toHaveBeenNthCalledWith(1, {
      x: 5,
      y: 6,
      direction: 'left',
      amount: 2,
      modifiers: ['cmd'],
    })
    expect(apiClient.scroll).toHaveBeenNthCalledWith(2, { x: 7, y: 8, direction: 'right', amount: 3 })
  })

  it('omits clicks and modifiers from the wire payload when not provided', async () => {
    apiClient.click.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.drag.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.scroll.mockResolvedValue(createApiResponse({ success: true }))

    await computerUse.mouse.click(10, 20)
    await computerUse.mouse.drag(1, 2, 3, 4)
    await computerUse.mouse.scroll(5, 6, 'down')

    const clickPayload = JSON.parse(JSON.stringify(apiClient.click.mock.calls[0][0]))
    const dragPayload = JSON.parse(JSON.stringify(apiClient.drag.mock.calls[0][0]))
    const scrollPayload = JSON.parse(JSON.stringify(apiClient.scroll.mock.calls[0][0]))

    // An omitted click count must not degrade into an explicit 0, which the daemon rejects.
    expect(Object.keys(clickPayload)).not.toContain('clicks')
    expect(Object.keys(clickPayload)).not.toContain('modifiers')
    expect(clickPayload).toEqual({ x: 10, y: 20, button: 'left', double: false })
    expect(Object.keys(dragPayload)).not.toContain('modifiers')
    expect(Object.keys(scrollPayload)).not.toContain('modifiers')
  })

  it('accepts the pre-change click, drag, and scroll argument shapes', async () => {
    apiClient.click.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.drag.mockResolvedValue(createApiResponse({ success: true }))
    apiClient.scroll.mockResolvedValue(createApiResponse({ success: true }))

    await expect(computerUse.mouse.click(10, 20)).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.click(10, 20, 'right')).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.click(10, 20, 'left', true)).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.drag(1, 2, 3, 4)).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.drag(1, 2, 3, 4, 'middle')).resolves.toEqual({ success: true })
    await expect(computerUse.mouse.scroll(5, 6, 'up')).resolves.toBe(true)
    await expect(computerUse.mouse.scroll(5, 6, 'down', 7)).resolves.toBe(true)

    expect(apiClient.click).toHaveBeenNthCalledWith(1, { x: 10, y: 20, button: 'left', double: false })
    expect(apiClient.click).toHaveBeenNthCalledWith(2, { x: 10, y: 20, button: 'right', double: false })
    expect(apiClient.click).toHaveBeenNthCalledWith(3, { x: 10, y: 20, button: 'left', double: true })
    expect(apiClient.drag).toHaveBeenNthCalledWith(1, { startX: 1, startY: 2, endX: 3, endY: 4, button: 'left' })
    expect(apiClient.drag).toHaveBeenNthCalledWith(2, { startX: 1, startY: 2, endX: 3, endY: 4, button: 'middle' })
    expect(apiClient.scroll).toHaveBeenNthCalledWith(1, { x: 5, y: 6, direction: 'up', amount: 1 })
    expect(apiClient.scroll).toHaveBeenNthCalledWith(2, { x: 5, y: 6, direction: 'down', amount: 7 })
  })

  it('passes explicit screenshot options through to the api', async () => {
    apiClient.takeCompressedScreenshot.mockResolvedValue(createApiResponse({ data: 'img' }))
    apiClient.takeCompressedRegionScreenshot.mockResolvedValue(createApiResponse({ data: 'region' }))

    await computerUse.screenshot.takeCompressed({ showCursor: true, format: 'jpeg', quality: 90, scale: 0.5 })
    await computerUse.screenshot.takeCompressedRegion(
      { x: 10, y: 20, width: 30, height: 40 },
      { showCursor: true, format: 'webp', quality: 80, scale: 2 },
    )

    expect(apiClient.takeCompressedScreenshot).toHaveBeenCalledWith(true, 'jpeg', 90, 0.5)
    expect(apiClient.takeCompressedRegionScreenshot).toHaveBeenCalledWith(10, 20, 30, 40, true, 'webp', 80, 2)
  })

  it('delegates recording operations including download stream', async () => {
    apiClient.startRecording.mockResolvedValue(createApiResponse({ id: 'r1' }))
    apiClient.stopRecording.mockResolvedValue(createApiResponse({ id: 'r1', status: 'completed' }))
    apiClient.listRecordings.mockResolvedValue(createApiResponse({ recordings: [{ id: 'r1' }] }))
    apiClient.getRecording.mockResolvedValue(createApiResponse({ id: 'r1', status: 'completed' }))
    apiClient.deleteRecording.mockResolvedValue(createApiResponse(undefined))
    apiClient.downloadRecording.mockResolvedValue(createApiResponse({ pipe: jest.fn() }))

    const writer = {}
    const fsModule = {
      promises: { mkdir: jest.fn() },
      createWriteStream: jest.fn(() => writer),
    }
    const streamModule = { pipeline: jest.fn((_from: unknown, _to: unknown, cb: (err?: Error) => void) => cb()) }
    const utilModule = { promisify: (_f: unknown) => async () => undefined }

    mockDynamicImport.mockImplementation(async (moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'stream') return streamModule
      if (moduleName === 'util') return utilModule
      return {}
    })

    await expect(computerUse.recording.start('test')).resolves.toEqual({ id: 'r1' })
    await expect(computerUse.recording.stop('r1')).resolves.toEqual({ id: 'r1', status: 'completed' })
    await expect(computerUse.recording.list()).resolves.toEqual({ recordings: [{ id: 'r1' }] })
    await expect(computerUse.recording.get('r1')).resolves.toEqual({ id: 'r1', status: 'completed' })
    await computerUse.recording.delete('r1')
    await computerUse.recording.download('r1', '/tmp/recordings/r1.mp4')
  })

  it('downloads recordings into the current directory without nested mkdirs', async () => {
    apiClient.downloadRecording.mockResolvedValue(createApiResponse({ stream: true }))

    const fsModule = {
      promises: { mkdir: jest.fn() },
      createWriteStream: jest.fn(() => ({ writer: true })),
    }
    const utilModule = { promisify: jest.fn(() => async () => undefined) }
    const streamModule = { pipeline: jest.fn() }

    mockDynamicImport.mockImplementation(async (moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'stream') return streamModule
      if (moduleName === 'util') return utilModule
      return {}
    })

    await computerUse.recording.download('r1', 'recording.mp4')

    expect(fsModule.promises.mkdir).toHaveBeenCalledWith('.', { recursive: true })
    expect(apiClient.downloadRecording).toHaveBeenCalledWith('r1', { responseType: 'stream' })
  })

  it('propagates recording and process control errors', async () => {
    const error = new Error('computer use failed')
    apiClient.startRecording.mockRejectedValue(error)
    apiClient.startComputerUse.mockRejectedValue(error)

    await expect(computerUse.recording.start('bad')).rejects.toBe(error)
    await expect(computerUse.start()).rejects.toBe(error)
  })

  it('delegates top-level computer-use process controls', async () => {
    apiClient.startComputerUse.mockResolvedValue(createApiResponse({ message: 'started' }))
    apiClient.stopComputerUse.mockResolvedValue(createApiResponse({ message: 'stopped' }))
    apiClient.getComputerUseStatus.mockResolvedValue(createApiResponse({ status: 'running' }))
    apiClient.getProcessStatus.mockResolvedValue(createApiResponse({ name: 'xvfb', running: true }))
    apiClient.restartProcess.mockResolvedValue(createApiResponse({ message: 'restarted' }))
    apiClient.getProcessLogs.mockResolvedValue(createApiResponse({ logs: 'ok' }))
    apiClient.getProcessErrors.mockResolvedValue(createApiResponse({ errors: '' }))

    await expect(computerUse.start()).resolves.toEqual({ message: 'started' })
    await expect(computerUse.stop()).resolves.toEqual({ message: 'stopped' })
    await expect(computerUse.getStatus()).resolves.toEqual({ status: 'running' })
    await expect(computerUse.getProcessStatus('xvfb')).resolves.toEqual({ name: 'xvfb', running: true })
    await expect(computerUse.restartProcess('xvfb')).resolves.toEqual({ message: 'restarted' })
    await expect(computerUse.getProcessLogs('xvfb')).resolves.toEqual({ logs: 'ok' })
    await expect(computerUse.getProcessErrors('xvfb')).resolves.toEqual({ errors: '' })
  })

  it('passes process names through to process control helpers', async () => {
    apiClient.getProcessStatus.mockResolvedValue(createApiResponse({ name: 'novnc', running: false }))
    apiClient.restartProcess.mockResolvedValue(createApiResponse({ message: 'done' }))
    apiClient.getProcessLogs.mockResolvedValue(createApiResponse({ logs: 'logs' }))
    apiClient.getProcessErrors.mockResolvedValue(createApiResponse({ errors: 'errors' }))

    await computerUse.getProcessStatus('novnc')
    await computerUse.restartProcess('novnc')
    await computerUse.getProcessLogs('novnc')
    await computerUse.getProcessErrors('novnc')

    expect(apiClient.getProcessStatus).toHaveBeenCalledWith('novnc')
    expect(apiClient.restartProcess).toHaveBeenCalledWith('novnc')
    expect(apiClient.getProcessLogs).toHaveBeenCalledWith('novnc')
    expect(apiClient.getProcessErrors).toHaveBeenCalledWith('novnc')
  })

  it('delegates accessibility operations and preserves optional values', async () => {
    apiClient.getAccessibilityTree.mockResolvedValue(createApiResponse({ root: { id: 'root' } }))
    apiClient.findAccessibilityNodes.mockResolvedValue(createApiResponse({ matches: [{ id: 'node-1' }] }))
    apiClient.focusAccessibilityNode.mockResolvedValue(createApiResponse(undefined))
    apiClient.invokeAccessibilityNode.mockResolvedValue(createApiResponse(undefined))
    apiClient.setAccessibilityNodeValue.mockResolvedValue(createApiResponse(undefined))

    await expect(computerUse.accessibility.getTree()).resolves.toEqual({ root: { id: 'root' } })
    await expect(computerUse.accessibility.getTree({ scope: 'pid', pid: 123, maxDepth: 0 })).resolves.toEqual({
      root: { id: 'root' },
    })
    await expect(
      computerUse.accessibility.findNodes({
        scope: 'all',
        role: 'button',
        name: 'Submit',
        nameMatch: 'exact',
        states: ['visible'],
        limit: 0,
      }),
    ).resolves.toEqual({ matches: [{ id: 'node-1' }] })

    await computerUse.accessibility.focusNode('node-1')
    await computerUse.accessibility.invokeNode('node-1')
    await computerUse.accessibility.invokeNode('node-2', 'click')
    await computerUse.accessibility.setNodeValue('node-3', 'hello')

    expect(apiClient.getAccessibilityTree).toHaveBeenNthCalledWith(1, undefined, undefined, undefined)
    expect(apiClient.getAccessibilityTree).toHaveBeenNthCalledWith(2, 'pid', 123, 0)
    expect(apiClient.findAccessibilityNodes).toHaveBeenCalledWith({
      scope: 'all',
      role: 'button',
      name: 'Submit',
      nameMatch: 'exact',
      states: ['visible'],
      limit: 0,
    })
    expect(apiClient.focusAccessibilityNode).toHaveBeenCalledWith({ id: 'node-1' })
    expect(apiClient.invokeAccessibilityNode).toHaveBeenNthCalledWith(1, { id: 'node-1', action: undefined })
    expect(apiClient.invokeAccessibilityNode).toHaveBeenNthCalledWith(2, { id: 'node-2', action: 'click' })
    expect(apiClient.setAccessibilityNodeValue).toHaveBeenCalledWith({ id: 'node-3', value: 'hello' })
  })
})
