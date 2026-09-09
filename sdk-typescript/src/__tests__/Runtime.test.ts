// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

import {
  DaytonaEnvReader,
  dotenvSearchDirs,
  endpointNamedInProcessEnv,
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

describe('dotenv endpoint detection', () => {
  let tmpDir: string
  let originalCwd: string
  let originalExecArgv: string[]
  let originalArgv: string[]
  let originalDotenvConfigPath: string | undefined

  beforeEach(() => {
    originalCwd = process.cwd()
    originalExecArgv = process.execArgv
    originalArgv = process.argv
    originalDotenvConfigPath = process.env.DOTENV_CONFIG_PATH
    delete process.env.DOTENV_CONFIG_PATH
    delete process.env.DAYTONA_API_URL
    delete process.env.DAYTONA_SERVER_URL
    tmpDir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'daytona-preload-'))
    process.chdir(tmpDir)
  })

  afterEach(() => {
    process.execArgv = originalExecArgv
    process.argv = originalArgv
    const restore = (name: string, value: string | undefined) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('DOTENV_CONFIG_PATH', originalDotenvConfigPath)
    delete process.env.DAYTONA_API_URL
    delete process.env.DAYTONA_SERVER_URL
    process.chdir(originalCwd)
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  describe('endpointNamedInProcessEnv', () => {
    it('is true when DAYTONA_API_URL is set', () => {
      process.env.DAYTONA_API_URL = 'https://example.com/api'

      expect(endpointNamedInProcessEnv()).toBe(true)
    })

    it('is true when DAYTONA_SERVER_URL is set', () => {
      process.env.DAYTONA_SERVER_URL = 'https://example.com/api'

      expect(endpointNamedInProcessEnv()).toBe(true)
    })

    it('is false when neither variable is set', () => {
      expect(endpointNamedInProcessEnv()).toBe(false)
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

    it('reports a file whose first line sits behind a byte-order mark', () => {
      fs.writeFileSync('.env', '\uFEFFDAYTONA_API_URL=https://elsewhere.invalid/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it('reports the deprecated DAYTONA_SERVER_URL too', () => {
      fs.writeFileSync('.env.local', 'DAYTONA_SERVER_URL=https://attacker.invalid/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env.local'))
    })

    it.each([
      ['an export prefix', 'export DAYTONA_API_URL=https://elsewhere.invalid/api\n'],
      ['blanks around the separator', '   DAYTONA_API_URL = https://elsewhere.invalid/api\n'],
      ['the KEY: value form the loader also accepts', 'DAYTONA_API_URL: https://elsewhere.invalid/api\n'],
      ['a quoted value', 'DAYTONA_API_URL="https://elsewhere.invalid/api"\n'],
      ['a trailing comment', 'DAYTONA_API_URL=https://elsewhere.invalid/api # note\n'],
      ['a later line', 'FOO=1\nDAYTONA_API_URL=https://elsewhere.invalid/api\n'],
      ['CRLF line endings', 'FOO=1\r\nDAYTONA_API_URL=https://elsewhere.invalid/api\r\n'],
      ['a line after a closed multiline value', 'FOO="a\nb"\nDAYTONA_API_URL=https://elsewhere.invalid/api\n'],
      // An unterminated quote ends at the newline for the loader, so the next line really
      // is an assignment.
      ['a line after an unterminated quote', 'FOO="abc\nDAYTONA_API_URL=https://elsewhere.invalid/api\n'],
      [
        'a line after a comment holding an apostrophe',
        "# don't worry\nDAYTONA_API_URL=https://elsewhere.invalid/api\n",
      ],
    ])('reports an endpoint written with %s', (_label, contents) => {
      fs.writeFileSync('.env', contents)

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it.each([
      ['an indented comment', '   # DAYTONA_API_URL=https://elsewhere.invalid/api\n'],
      ['a multiline double-quoted value', 'FOO="one\nDAYTONA_API_URL=unused\nthree"\n'],
      ['a multiline single-quoted value', "FOO='one\nDAYTONA_API_URL=unused\nthree'\n"],
      ['a multiline backtick value', 'FOO=`one\nDAYTONA_API_URL=unused\nthree`\n'],
      ['a key that merely ends with the name', 'MY_DAYTONA_API_URL=https://elsewhere.invalid/api\n'],
      ['a key that merely starts with the name', 'DAYTONA_API_URL_EXTRA=https://elsewhere.invalid/api\n'],
      ['a bare name with no separator', 'DAYTONA_API_URL\n'],
      // The loader requires whitespace after a colon, so this assigns nothing and there is
      // nothing to report.
      ['a colon with no following blank', 'DAYTONA_API_URL:https://elsewhere.invalid/api\n'],
    ])('ignores %s', (_label, contents) => {
      fs.writeFileSync('.env', contents)

      expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
    })

    it('scans each file independently', () => {
      fs.writeFileSync('.env.production', 'FOO=1\nBAR=2\nDAYTONA_API_URL=https://elsewhere.invalid/api\n')
      fs.writeFileSync('.env', 'DAYTONA_API_URL=https://elsewhere.invalid/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))

      fs.rmSync('.env')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env.production'))
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

    it('examines the path the dotenv preloader was pointed at via DOTENV_CONFIG_PATH', () => {
      fs.writeFileSync('preloaded.env', 'DAYTONA_API_URL=https://attacker.invalid/api\n')
      process.execArgv = ['-r', 'dotenv/config']
      process.env.DOTENV_CONFIG_PATH = 'preloaded.env'

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, 'preloaded.env'))
    })

    it('examines the path given as a dotenv_config_path argument', () => {
      fs.writeFileSync('preloaded.env', 'DAYTONA_API_URL=https://attacker.invalid/api\n')
      process.execArgv = ['-r', 'dotenv/config']
      process.argv = [...process.argv, 'dotenv_config_path=preloaded.env']

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, 'preloaded.env'))
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

    it('detects a bare DAYTONA_API_URL assignment with no preload flags set', () => {
      process.execArgv = []
      fs.writeFileSync('.env', 'DAYTONA_API_URL=https://example.com/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it('detects the export form of an endpoint assignment', () => {
      fs.writeFileSync('.env', 'export DAYTONA_API_URL=https://example.com/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it('does not detect a commented-out assignment', () => {
      fs.writeFileSync('.env', '# DAYTONA_API_URL=https://example.com/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
    })

    it('does not detect a file naming neither endpoint variable', () => {
      fs.writeFileSync('.env', 'SOME_OTHER_VAR=https://example.com/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBeUndefined()
    })

    it('detects DAYTONA_SERVER_URL', () => {
      fs.writeFileSync('.env', 'DAYTONA_SERVER_URL=https://example.com/api\n')

      expect(findDotenvFileDefiningEndpoint()).toBe(path.join(tmpDir, '.env'))
    })

    it('succeeds when the dotenv package cannot be resolved', () => {
      fs.writeFileSync('.env', 'DAYTONA_API_URL=https://example.com/api\n')
      let result: string | undefined
      jest.isolateModules(() => {
        jest.doMock('dotenv', () => {
          throw new Error("Cannot find module 'dotenv'")
        })
        const { findDotenvFileDefiningEndpoint: find } = require('../utils/Runtime')
        result = find([tmpDir])
      })
      expect(result).toBe(path.join(tmpDir, '.env'))
    })
  })
})
