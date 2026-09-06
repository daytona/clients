// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

import { DaytonaEnvReader, warnIfDotenvApiUrlIgnored } from '../utils/Runtime'

const DEFAULT_API_URL = 'https://app.daytona.io/api'

// Exercises the real reader against real files on disk. Daytona.test.ts mocks the reader
// for determinism, so without this the cwd-relative parsing itself would go untested.
describe('DaytonaEnvReader', () => {
  let tmpDir: string
  let originalCwd: string

  beforeEach(() => {
    originalCwd = process.cwd()
    tmpDir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-env-'))
    process.chdir(tmpDir)
    delete process.env.DAYTONA_API_URL
    delete process.env.DAYTONA_SERVER_URL
  })

  afterEach(() => {
    process.chdir(originalCwd)
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  it('reads dotenv files relative to the working directory', () => {
    fs.writeFileSync('.env', 'DAYTONA_API_URL=http://attacker.example/api\n')

    const reader = new DaytonaEnvReader()

    expect(reader.getFromFile('DAYTONA_API_URL')).toBe('http://attacker.example/api')
  })

  it('does not expose dotenv values through the process-environment accessor', () => {
    fs.writeFileSync('.env', 'DAYTONA_API_URL=http://attacker.example/api\n')
    fs.writeFileSync('.env.local', 'DAYTONA_SERVER_URL=http://attacker.example/api\n')

    const reader = new DaytonaEnvReader()

    expect(reader.getFromProcessEnv('DAYTONA_API_URL')).toBeUndefined()
    expect(reader.getFromProcessEnv('DAYTONA_SERVER_URL')).toBeUndefined()
  })

  it('prefers the process environment over dotenv files in the general accessor', () => {
    process.env.DAYTONA_API_URL = 'https://runtime.example/api'
    fs.writeFileSync('.env', 'DAYTONA_API_URL=http://attacker.example/api\n')

    const reader = new DaytonaEnvReader()

    expect(reader.get('DAYTONA_API_URL')).toBe('https://runtime.example/api')
    expect(reader.getFromProcessEnv('DAYTONA_API_URL')).toBe('https://runtime.example/api')
    expect(reader.getFromFile('DAYTONA_API_URL')).toBe('http://attacker.example/api')
  })

  // Bun merges the working directory's .env into process.env before user code runs, as do
  // Next.js and `node --env-file`. These cover that shape without needing those runtimes:
  // the observable condition is a process value identical to the file's.
  it('does not trust a process value a dotenv file could account for', () => {
    fs.writeFileSync('.env', 'DAYTONA_API_URL=http://attacker.example/api\n')
    process.env.DAYTONA_API_URL = 'http://attacker.example/api'

    const reader = new DaytonaEnvReader()

    expect(reader.getFromFile('DAYTONA_API_URL')).toBe('http://attacker.example/api')
    expect(reader.getFromProcessEnv('DAYTONA_API_URL')).toBeUndefined()
  })

  it('trusts a process value that differs from the dotenv file', () => {
    fs.writeFileSync('.env', 'DAYTONA_API_URL=http://attacker.example/api\n')
    process.env.DAYTONA_API_URL = 'https://chosen-by-shell.example/api'

    const reader = new DaytonaEnvReader()

    expect(reader.getFromProcessEnv('DAYTONA_API_URL')).toBe('https://chosen-by-shell.example/api')
  })

  it('trusts a process value when no dotenv file is present', () => {
    process.env.DAYTONA_API_URL = 'https://chosen-by-shell.example/api'

    const reader = new DaytonaEnvReader()

    expect(reader.getFromProcessEnv('DAYTONA_API_URL')).toBe('https://chosen-by-shell.example/api')
  })

  it('rejects variable names outside the DAYTONA_ namespace', () => {
    const reader = new DaytonaEnvReader()

    expect(() => reader.getFromProcessEnv('OTHER_VAR')).toThrow("must start with 'DAYTONA_'")
    expect(() => reader.getFromFile('OTHER_VAR')).toThrow("must start with 'DAYTONA_'")
  })
})

describe('warnIfDotenvApiUrlIgnored', () => {
  let tmpDir: string
  let originalCwd: string
  let warnSpy: jest.SpyInstance

  beforeEach(() => {
    originalCwd = process.cwd()
    tmpDir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-env-'))
    process.chdir(tmpDir)
    delete process.env.DAYTONA_API_URL
    delete process.env.DAYTONA_SERVER_URL
    warnSpy = jest.spyOn(console, 'warn').mockImplementation((): void => undefined)
  })

  afterEach(() => {
    warnSpy.mockRestore()
    process.chdir(originalCwd)
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  it('reports a dotenv endpoint that differs from the one in use', () => {
    fs.writeFileSync('.env', 'DAYTONA_API_URL=http://attacker.example/api\n')

    warnIfDotenvApiUrlIgnored(new DaytonaEnvReader(), DEFAULT_API_URL)

    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(String(warnSpy.mock.calls[0][0])).toContain('DAYTONA_API_URL')
    expect(String(warnSpy.mock.calls[0][0])).toContain('was ignored')
  })

  it('stays quiet when the dotenv endpoint matches the one in use', () => {
    fs.writeFileSync('.env', `DAYTONA_API_URL=${DEFAULT_API_URL}\n`)

    warnIfDotenvApiUrlIgnored(new DaytonaEnvReader(), DEFAULT_API_URL)

    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('stays quiet when no dotenv file sets an endpoint', () => {
    warnIfDotenvApiUrlIgnored(new DaytonaEnvReader(), DEFAULT_API_URL)

    expect(warnSpy).not.toHaveBeenCalled()
  })
})
