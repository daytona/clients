// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

import {
  DaytonaEnvReader,
  dotenvMayBePreloaded,
  dotenvSearchDirs,
  findDotenvFileDefiningEndpoint,
  warnIfDotenvApiUrlIgnored,
} from '../utils/Runtime'

const DEFAULT_API_URL = 'https://app.daytona.io/api'
// Captured at module load, mirroring how Runtime.ts captures its startup directory.
const MODULE_LOAD_CWD = process.cwd()

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

  it('reports once when a file sets both endpoint variables to the same value', () => {
    fs.writeFileSync(
      '.env',
      'DAYTONA_API_URL=http://attacker.example/api\nDAYTONA_SERVER_URL=http://attacker.example/api\n',
    )

    warnIfDotenvApiUrlIgnored(new DaytonaEnvReader(), DEFAULT_API_URL)

    expect(warnSpy).toHaveBeenCalledTimes(1)
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

// Runtimes that load a dotenv file into process.env before user code runs make the process
// environment unattributable. These cover the detection that decides whether to refuse.
describe('dotenv pre-loading detection', () => {
  let tmpDir: string
  let originalCwd: string
  let originalExecArgv: string[]
  let originalNextRuntime: string | undefined

  beforeEach(() => {
    originalCwd = process.cwd()
    originalExecArgv = process.execArgv
    originalNextRuntime = process.env.NEXT_RUNTIME
    delete process.env.NEXT_RUNTIME
    tmpDir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-preload-'))
    process.chdir(tmpDir)
  })

  afterEach(() => {
    process.execArgv = originalExecArgv
    if (originalNextRuntime === undefined) delete process.env.NEXT_RUNTIME
    else process.env.NEXT_RUNTIME = originalNextRuntime
    process.chdir(originalCwd)
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  describe('dotenvMayBePreloaded', () => {
    it('is false for a plain node process', () => {
      process.execArgv = []

      expect(dotenvMayBePreloaded()).toBe(false)
    })

    it('is true when node was given --env-file', () => {
      process.execArgv = ['--env-file=.env']

      expect(dotenvMayBePreloaded()).toBe(true)
    })

    it('is true when node was given --env-file-if-exists', () => {
      process.execArgv = ['--env-file-if-exists=.env']

      expect(dotenvMayBePreloaded()).toBe(true)
    })

    it('is true inside a Next.js server runtime', () => {
      process.execArgv = []
      process.env.NEXT_RUNTIME = 'nodejs'

      expect(dotenvMayBePreloaded()).toBe(true)
    })
  })

  describe('findDotenvFileDefiningEndpoint', () => {
    it('reports a file that sets the endpoint', () => {
      fs.writeFileSync('.env', 'DAYTONA_API_URL=https://attacker.invalid/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it('reports a file the SDK does not otherwise read, such as .env.production', () => {
      fs.writeFileSync('.env.production', 'DAYTONA_API_URL=https://attacker.invalid/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env.production'))
    })

    it('reports the deprecated DAYTONA_SERVER_URL too', () => {
      fs.writeFileSync('.env.local', 'DAYTONA_SERVER_URL=https://attacker.invalid/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env.local'))
    })

    it('is not defeated by a value assembled from another variable', () => {
      // A parser returns the raw text while the runtime expands it, so only the presence of
      // the name is meaningful here.
      fs.writeFileSync(
        '.env',
        'DAYTONA_REVIEW_HOST=attacker.invalid\nDAYTONA_API_URL=https://$DAYTONA_REVIEW_HOST/api\n',
      )

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it('reports nothing when no dotenv file names the endpoint', () => {
      fs.writeFileSync('.env', 'DAYTONA_API_KEY=some-key\nDAYTONA_TARGET=us\n')

      expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
    })

    it('reports nothing when there is no dotenv file at all', () => {
      expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
    })

    it('examines a path named by --env-file, not just the conventional names', () => {
      fs.mkdirSync('elsewhere')
      fs.writeFileSync('elsewhere/custom.env', 'DAYTONA_API_URL=https://attacker.invalid/api\n')
      process.execArgv = ['--env-file=elsewhere/custom.env']

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, 'elsewhere/custom.env'))
    })

    it('examines an absolute path named by --env-file', () => {
      const absolute = path.join(tmpDir, 'abs.env')
      fs.writeFileSync(absolute, 'DAYTONA_SERVER_URL=https://attacker.invalid/api\n')
      process.execArgv = [`--env-file=${absolute}`]

      expect(findDotenvFileDefiningEndpoint()).toBe(absolute)
    })

    it('examines a path given as a separate --env-file argument', () => {
      fs.writeFileSync('custom.env', 'DAYTONA_API_URL=https://attacker.invalid/api\n')
      process.execArgv = ['--env-file', 'custom.env']

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, 'custom.env'))
    })

    it('still finds the file after the application changes directory', () => {
      // A runtime resolves its dotenv files at startup, so process.chdir() afterwards must
      // not move the search away from the file that supplied the environment. The startup
      // directory is captured when the module loads, which under jest is the package root,
      // so the directory under test is passed in explicitly.
      fs.writeFileSync('.env', 'DAYTONA_API_URL=https://attacker.invalid/api\n')
      const elsewhere = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-elsewhere-'))
      try {
        process.chdir(elsewhere)

        expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
        expect(findDotenvFileDefiningEndpoint([tmpDir])).toBe(path.join(tmpDir, '.env'))
      } finally {
        process.chdir(tmpDir)
        fs.rmSync(elsewhere, { recursive: true, force: true })
      }
    })

    it('searches the startup directory as well as the current one', () => {
      // MODULE_LOAD_CWD is captured at the top of this file, at the same point in the
      // lifecycle as Runtime.ts captures its own, so after a chdir the two must both appear.
      process.chdir(tmpDir)

      const dirs = dotenvSearchDirs()

      expect(dirs).toContain(fs.realpathSync(tmpDir))
      expect(dirs).toContain(MODULE_LOAD_CWD)
      expect(dirs).toHaveLength(2)
    })

    it('returns a single directory when the application has not changed directory', () => {
      process.chdir(MODULE_LOAD_CWD)

      expect(dotenvSearchDirs()).toEqual([MODULE_LOAD_CWD])
    })

    it('reports nothing when the --env-file target does not name the endpoint', () => {
      fs.writeFileSync('custom.env', 'SOMETHING_ELSE=1\n')
      process.execArgv = ['--env-file=custom.env']

      expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
    })
  })
})
