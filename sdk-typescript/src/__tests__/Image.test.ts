// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

const mockDynamicRequire = jest.fn()

jest.mock('../utils/Import', () => ({
  dynamicRequire: (...args: unknown[]) => mockDynamicRequire(...args),
}))

describe('Image', () => {
  // extractCopySources resolves candidates through fs, so every dynamicRequire mock supplies it
  const fsStub = { realpathSync: (target: string) => target, lstatSync: () => undefined }
  const { isDynamicPattern } = jest.requireActual<typeof import('tinyglobby')>('tinyglobby')

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('builds images using static factories', async () => {
    const { Image } = await import('../Image')

    const base = Image.base('debian:12')
    expect(base.dockerfile).toContain('FROM debian:12')

    const slim = Image.debianSlim('3.12')
    expect(slim.dockerfile).toContain('FROM python:3.12.10-slim-bookworm')
  })

  it('uses the latest supported python version by default', async () => {
    const { Image } = await import('../Image')

    const slim = Image.debianSlim()
    expect(slim.dockerfile).toContain('FROM python:3.13.3-slim-bookworm')
  })

  it('supports dockerfile mutation methods', async () => {
    const { Image } = await import('../Image')

    const image = Image.base('python:3.12')
      .pipInstall(['numpy', 'pandas'])
      .runCommands('echo hi', ['bash', '-lc', 'pwd'])
      .env({ NODE_ENV: 'test' })
      .workdir('/workspace')
      .entrypoint(['/bin/bash'])
      .cmd(['-lc', 'echo ready'])

    expect(image.dockerfile).toContain('pip install')
    expect(image.dockerfile).toContain('RUN echo hi')
    expect(image.dockerfile).toContain('ENV NODE_ENV')
    expect(image.dockerfile).toContain('WORKDIR')
    expect(image.dockerfile).toContain('ENTRYPOINT')
    expect(image.dockerfile).toContain('CMD')
  })

  it('formats pip install options and sorts package names', async () => {
    const { Image } = await import('../Image')

    const image = Image.base('python:3.12').pipInstall(['pandas', 'numpy'], {
      findLinks: ['https://links.example.com'],
      indexUrl: 'https://index.example.com/simple',
      extraIndexUrls: ['https://extra.example.com/simple'],
      pre: true,
      extraOptions: '  --no-cache-dir  ',
    })

    expect(image.dockerfile).toContain('pip install numpy pandas')
    expect(image.dockerfile).toContain('--find-links')
    expect(image.dockerfile).toContain('--index-url')
    expect(image.dockerfile).toContain('--extra-index-url')
    expect(image.dockerfile).toContain('--pre')
    expect(image.dockerfile).toContain('--no-cache-dir')
  })

  it('does not modify the dockerfile when pipInstall receives no packages', async () => {
    const { Image } = await import('../Image')

    const image = Image.base('python:3.12')
    const before = image.dockerfile
    image.pipInstall([])

    expect(image.dockerfile).toBe(before)
  })

  it('validates entrypoint/cmd/env input', async () => {
    const { Image } = await import('../Image')
    expect(() => Image.base('x').entrypoint([1] as unknown as string[])).toThrow(
      'entrypoint_commands must be a list of strings',
    )
    expect(() => Image.base('x').cmd([1] as unknown as string[])).toThrow('Image CMD must be a list of strings')
    expect(() => Image.base('x').env({ A: 1 as unknown as string })).toThrow('Image ENV variables must be strings')
  })

  it('validates unsupported python versions', async () => {
    const { Image } = await import('../Image')

    expect(() => Image.debianSlim('3.8' as never)).toThrow('Unsupported Python version: 3.8')
  })

  it('validates nested non-string pipInstall arguments', async () => {
    const { Image } = await import('../Image')
    const runtime = Image.base('python:3.12') as unknown as { pipInstall: (packages: unknown) => unknown }
    const badPackages: unknown = ['numpy', [123]]

    expect(() => runtime.pipInstall(badPackages)).toThrow('pipInstall: packages must only contain strings')
  })

  it('validates dockerfile entrypoint command arrays', async () => {
    const { Image } = await import('../Image')

    expect(() => Image.base('x').entrypoint('bash' as never)).toThrow('entrypoint_commands must be a list of strings')
  })

  it('supports fromDockerfile and local context additions', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn(() => true),
      readFileSync: jest.fn(() => 'FROM debian:12\nCOPY ./src /app/src\n'),
      statSync: jest.fn(() => ({ isDirectory: () => true, isFile: () => true })),
      realpathSync: jest.fn((target: string) => target),
      lstatSync: jest.fn(() => undefined),
    }
    const expandTilde = (value: string) => value
    const tinyglobby = { isDynamicPattern, globSync: jest.fn(() => ['src']) }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return expandTilde
      if (moduleName === 'tinyglobby') return tinyglobby
      return {}
    })

    const image = Image.fromDockerfile('/repo/Dockerfile')
      .addLocalFile('/repo/.env', '/workspace/')
      .addLocalDir('/repo/src', '/workspace/src')
      .dockerfileCommands(['COPY ./assets /app/assets'], '/repo')

    expect(image.contextList.length).toBeGreaterThan(0)
    expect(image.dockerfile).toContain('COPY')
  })

  it('throws when fromDockerfile path does not exist', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn(() => false),
      statSync: jest.fn(),
      readFileSync: jest.fn(),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      return {}
    })

    expect(() => Image.fromDockerfile('/repo/Dockerfile')).toThrow('Dockerfile /repo/Dockerfile does not exist')
  })

  it('throws when local file or directory inputs are invalid', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn((path: string) => path !== '/missing'),
      statSync: jest.fn((path: string) => ({
        isDirectory: () => path === '/repo/file.txt',
        isFile: () => path === '/repo/dir',
      })),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      return {}
    })

    expect(() => Image.base('x').addLocalFile('/missing', '/tmp/')).toThrow('Local file /missing does not exist')
    expect(() => Image.base('x').addLocalFile('/repo/file.txt', '/tmp/')).toThrow(
      'Local path /repo/file.txt exists but is not a file',
    )
    expect(() => Image.base('x').addLocalDir('/repo/dir', '/tmp/dir')).toThrow(
      'Local path /repo/dir exists but is not a directory',
    )
  })

  it('validates dockerfileCommands context directories', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn((path: string) => path !== '/missing'),
      statSync: jest.fn((path: string) => ({
        isDirectory: () => path === '/repo',
      })),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      if (moduleName === 'tinyglobby') return { isDynamicPattern, globSync: jest.fn(() => []) }
      return {}
    })

    expect(() => Image.base('x').dockerfileCommands(['COPY ./src /app/src'], '/missing')).toThrow(
      'Context directory /missing does not exist',
    )
    expect(() => Image.base('x').dockerfileCommands(['COPY ./src /app/src'], '/file')).toThrow(
      'Context path /file exists but is not a directory',
    )
  })

  it('supports requirements files and pyproject dependencies', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn(() => true),
      statSync: jest.fn(() => ({ isFile: () => true })),
      readFileSync: jest.fn(() =>
        [
          '[project]',
          'dependencies = ["requests", "numpy"]',
          '[project.optional-dependencies]',
          'dev = ["pytest"]',
        ].join('\n'),
      ),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      if (moduleName === '@iarna/toml')
        return {
          parse: jest.fn(() => ({
            project: { dependencies: ['requests', 'numpy'], 'optional-dependencies': { dev: ['pytest'] } },
          })),
        }
      return {}
    })

    const image = Image.base('python:3.12')
      .pipInstallFromRequirements('requirements.txt')
      .pipInstallFromPyproject('pyproject.toml', { optionalDependencies: ['dev'] })

    expect(image.dockerfile).toContain('COPY requirements.txt /.requirements.txt')
    expect(image.dockerfile).toContain('pip install -r /.requirements.txt')
    expect(image.dockerfile).toContain('pip install numpy pytest requests')
  })

  it('validates missing or malformed pyproject files', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn(() => true),
      statSync: jest.fn(() => ({ isFile: () => true })),
      readFileSync: jest.fn(() => 'invalid = ['),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      if (moduleName === '@iarna/toml')
        return {
          parse: jest.fn(() => {
            throw new Error('Unexpected end of input')
          }),
        }
      return {}
    })

    expect(() => Image.base('python:3.12').pipInstallFromPyproject('pyproject.toml')).toThrow(
      'Invalid pyproject.toml file pyproject.toml: Unexpected end of input',
    )
  })

  it('validates pyproject files without dependencies metadata', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn(() => true),
      statSync: jest.fn(() => ({ isFile: () => true })),
      readFileSync: jest.fn(() => '[project]'),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      if (moduleName === '@iarna/toml') return { parse: jest.fn(() => ({ project: {} })) }
      return {}
    })

    expect(() => Image.base('python:3.12').pipInstallFromPyproject('pyproject.toml')).toThrow(
      'No [project.dependencies] section in pyproject.toml file.',
    )
  })

  it('validates malformed optional-dependencies in pyproject files', async () => {
    const { Image } = await import('../Image')

    const fsModule = {
      existsSync: jest.fn(() => true),
      statSync: jest.fn(() => ({ isFile: () => true })),
      readFileSync: jest.fn(() => '[project]'),
    }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      if (moduleName === '@iarna/toml') {
        return {
          parse: jest.fn(() => ({ project: { dependencies: ['requests'], 'optional-dependencies': ['bad'] } })),
        }
      }
      return {}
    })

    expect(() =>
      Image.base('python:3.12').pipInstallFromPyproject('pyproject.toml', { optionalDependencies: ['dev'] }),
    ).toThrow('optional-dependencies must be a mapping in pyproject.toml')
  })

  it('extractCopySources and parseCopyCommand handle copy variants', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn(() => ['a.txt']) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    const parsed = imageRuntime.parseCopyCommand('COPY --chown=user:group ./a.txt /app/a.txt') as {
      sources: string[]
      dest: string
    }
    expect(parsed.sources).toEqual(['./a.txt'])
    expect(parsed.dest).toBe('/app/a.txt')

    const sources = imageRuntime.extractCopySources('COPY ./a.txt /app/a.txt', '/repo') as Array<[string, string]>
    expect(sources[0]).toEqual(['/repo/a.txt', './a.txt'])
  })

  it('extractCopySources reads escaping sources by default and rejects them when strict', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => [patterns[0]]) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    // Default: today's resolution, an escaping source is read as written
    expect(imageRuntime.extractCopySources('COPY ../secret.txt /app/', '/repo')).toEqual([
      ['/secret.txt', '../secret.txt'],
    ])
    expect(imageRuntime.extractCopySources('COPY /etc/hosts /app/', '/repo')).toEqual([['/etc/hosts', '/etc/hosts']])

    // Strict: the same sources are rejected rather than read, whether or not they exist
    for (const source of ['../secret.txt', '/etc/hosts', '../nope.txt', '/nonexistent/secret.txt']) {
      expect(() => imageRuntime.extractCopySources(`COPY ${source} /app/`, '/repo', true)).toThrow(
        `forbidden path outside the build context: ${source}`,
      )
    }
  })

  it('extractCopySources rejects a source reached through a symlinked directory when strict', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn(() => ['secret.txt']) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') {
        return {
          realpathSync: (target: string) => (target === '/repo/dirlink/secret.txt' ? '/outside/secret.txt' : target),
          lstatSync: () => undefined,
        }
      }
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    expect(() => imageRuntime.extractCopySources('COPY dirlink/secret.txt /app/', '/repo', true)).toThrow(
      'forbidden path outside the build context: /outside/secret.txt',
    )

    // Without strictContext the same source is read as written
    expect(imageRuntime.extractCopySources('COPY dirlink/secret.txt /app/', '/repo')).toEqual([
      ['/repo/dirlink/secret.txt', 'dirlink/secret.txt'],
    ])
  })

  it('fromDockerfile and dockerfileCommands archive the context root for a dot operand', async () => {
    const { Image } = await import('../Image')
    const pathe = await import('pathe')

    const fsModule = {
      existsSync: jest.fn(() => true),
      readFileSync: jest.fn(() => 'FROM debian:12\nCOPY . /app/\n'),
      statSync: jest.fn(() => ({ isDirectory: () => true, isFile: () => true })),
      realpathSync: jest.fn((target: string) => target),
      lstatSync: jest.fn(() => undefined),
    }
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => [patterns[0]]) }

    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value
      if (moduleName === 'tinyglobby') return tinyglobby
      return {}
    })

    const fromDockerfile = Image.fromDockerfile('/repo/Dockerfile')
    const viaCommands = Image.base('debian:12').dockerfileCommands(['COPY . /app/'], '/repo')

    for (const image of [fromDockerfile, viaCommands]) {
      expect(image.contextList).toHaveLength(1)
      expect(image.contextList[0].sourcePath).toBe('/repo')
      // An empty archive path normalises to '.', which is the form ObjectStorage.uploadAsTar
      // handles explicitly when the source is the context root itself.
      expect(pathe.normalize(image.contextList[0].archivePath)).toBe('.')
    }
  })

  it('dockerfileCommands requires a context directory when strictContext is set', async () => {
    const { Image } = await import('../Image')

    expect(() =>
      Image.base('debian:12').dockerfileCommands(['COPY a.txt /app/'], undefined, { strictContext: true }),
    ).toThrow('strictContext requires contextDir')
  })

  it('dockerfileCommands resolves strict sources against the expanded context directory', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => [patterns[0]]) }
    const fsModule = {
      existsSync: jest.fn(() => true),
      statSync: jest.fn(() => ({ isDirectory: () => true, isFile: () => true })),
      realpathSync: jest.fn((target: string) => target),
      lstatSync: jest.fn(() => undefined),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsModule
      if (moduleName === 'expand-tilde') return (value: string) => value.replace('~', '/home/user')
      return {}
    })

    const image = Image.base('debian:12').dockerfileCommands(['COPY a.txt /app/'], '~/repo', {
      strictContext: true,
    })

    expect(image.contextList).toEqual([{ sourcePath: '/home/user/repo/a.txt', archivePath: 'a.txt' }])
  })

  it('parseCopyCommand handles json array copy commands', async () => {
    const { Image } = await import('../Image')
    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    const parsed = imageRuntime.parseCopyCommand('COPY ["./a.txt", "./b.txt", "/app/"]') as {
      sources: string[]
      dest: string
    }

    expect(parsed).toEqual({ sources: ['./a.txt', './b.txt'], dest: '/app/' })
  })

  it('extractCopySources joins backslash-continued COPY instructions', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => patterns) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>
    const sources = imageRuntime.extractCopySources(
      ['COPY a.txt \\', '    b.txt \\', '    /app/'].join('\n'),
      '/repo',
    ) as Array<[string, string]>

    expect(sources).toEqual([
      ['/repo/a.txt', 'a.txt'],
      ['/repo/b.txt', 'b.txt'],
    ])
  })

  it('extractCopySources ignores comment lines inside a continued COPY instruction', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => patterns) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>
    const sources = imageRuntime.extractCopySources(
      ['COPY a.txt \\', '    # a comment inside the instruction', '    /app/'].join('\n'),
      '/repo',
    ) as Array<[string, string]>

    expect(sources).toEqual([['/repo/a.txt', 'a.txt']])
  })

  it('extractCopySources ignores blank lines inside a continued COPY instruction', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => patterns) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>
    const sources = imageRuntime.extractCopySources(['COPY a.txt \\', '', '    /app/'].join('\n'), '/repo') as Array<
      [string, string]
    >

    expect(sources).toEqual([['/repo/a.txt', 'a.txt']])
  })

  it('extractCopySources does not continue a comment line that ends with a backslash', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn((patterns: string[]) => patterns) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>
    const sources = imageRuntime.extractCopySources(['# see C:\\', 'COPY a.txt /app/'].join('\n'), '/repo') as Array<
      [string, string]
    >

    expect(sources).toEqual([['/repo/a.txt', 'a.txt']])
  })

  it('extractCopySources ignores heredoc and stage copy commands', async () => {
    const { Image } = await import('../Image')
    const tinyglobby = { isDynamicPattern, globSync: jest.fn(() => ['b.txt']) }
    mockDynamicRequire.mockImplementation((moduleName: string) => {
      if (moduleName === 'tinyglobby') return tinyglobby
      if (moduleName === 'fs') return fsStub
      return {}
    })

    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>
    const sources = imageRuntime.extractCopySources(
      ['COPY --from=builder /tmp/a.txt /app/a.txt', 'COPY <<EOF /app/a.txt', 'COPY ./b.txt /app/b.txt'].join('\n'),
      '/repo',
    ) as Array<[string, string]>

    expect(sources).toEqual([['/repo/b.txt', './b.txt']])
  })

  it('parseCopyCommand keeps the sources after a boolean flag such as --link', async () => {
    const { Image } = await import('../Image')
    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    const parsed = imageRuntime.parseCopyCommand('COPY --link a.txt /app/')

    expect(parsed).toEqual({ sources: ['a.txt'], dest: '/app/' })
  })

  it('parseCopyCommand handles flags before a json array copy command', async () => {
    const { Image } = await import('../Image')
    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    const parsed = imageRuntime.parseCopyCommand('COPY --chown=1000:1000 --link ["a.txt", "/app/"]')

    expect(parsed).toEqual({ sources: ['a.txt'], dest: '/app/' })
  })

  it('parseCopyCommand keeps unquoted glob sources in a shell form copy command', async () => {
    const { Image } = await import('../Image')
    const imageRuntime = Image as unknown as Record<string, (...args: unknown[]) => unknown>

    expect(imageRuntime.parseCopyCommand('COPY src/*.txt /app/')).toEqual({
      sources: ['src/*.txt'],
      dest: '/app/',
      optionalSources: new Set([0]),
    })
    expect(imageRuntime.parseCopyCommand('COPY src/*.txt a.txt /app/')).toEqual({
      sources: ['src/*.txt', 'a.txt'],
      dest: '/app/',
      optionalSources: new Set([0]),
    })
    expect(imageRuntime.parseCopyCommand('COPY --chown=1000:1000 src/?.md /app/')).toEqual({
      sources: ['src/?.md'],
      dest: '/app/',
      optionalSources: new Set([0]),
    })
    // A line with a variable is parsed as before, so its glob tokens are dropped
    expect(imageRuntime.parseCopyCommand('COPY $SRC/*.txt $DEST')).toBeNull()
    expect(imageRuntime.parseCopyCommand('COPY src/*.txt $X.txt /app/')).toEqual({ sources: ['.txt'], dest: '/app/' })
    // Unquoted parentheses split the word, so its glob piece is not kept as a source rooted at /
    const grouped = imageRuntime.parseCopyCommand('COPY src/(group)/* /app/') as { sources: string[] }
    expect(grouped.sources).not.toContain('/*')
    expect(imageRuntime.parseCopyCommand('COPY "app/(marketing)/*" /app/')).toEqual({
      sources: ['app/(marketing)/*'],
      dest: '/app/',
    })
    // A glob outside the build context is dropped, so it never expands host paths
    expect(imageRuntime.parseCopyCommand('COPY /etc/*.conf /app/')).toBeNull()
    expect(imageRuntime.parseCopyCommand('COPY ../*.txt a.txt /app/')).toEqual({ sources: ['a.txt'], dest: '/app/' })
    // A glob in the destination position stays the destination, and the source before it, which
    // used to be read as the destination, is optional
    expect(imageRuntime.parseCopyCommand('COPY a.txt src/*.txt /opt/app*/')).toEqual({
      sources: ['a.txt', 'src/*.txt'],
      dest: '/opt/app*/',
      optionalSources: new Set([0, 1]),
    })
    expect(imageRuntime.parseCopyCommand('COPY /etc/hosts dst*')).toBeNull()
  })

  it('shell form COPY globs upload the same build context as the json array form', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    try {
      for (const file of ['a.txt', 'src/a.txt', 'src/b.txt', 'src/c.md', 'src/sub/d.txt', 'lib/e.txt']) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }

      const cases: Array<{ shellForm: string; jsonForm: string; archivePaths: string[] }> = [
        {
          shellForm: 'COPY src/*.txt /app/',
          jsonForm: 'COPY ["src/*.txt", "/app/"]',
          archivePaths: ['src/a.txt', 'src/b.txt'],
        },
        {
          shellForm: 'COPY src/*.txt a.txt /app/',
          jsonForm: 'COPY ["src/*.txt", "a.txt", "/app/"]',
          archivePaths: ['a.txt', 'src/a.txt', 'src/b.txt'],
        },
        {
          shellForm: 'COPY --chown=1000:1000 src/?.md /app/',
          jsonForm: 'COPY --chown=1000:1000 ["src/?.md", "/app/"]',
          archivePaths: ['src/c.md'],
        },
        {
          // A directory matches too and is uploaded with everything below it, as Docker copies it
          shellForm: 'COPY src/* /app/',
          jsonForm: 'COPY ["src/*", "/app/"]',
          archivePaths: ['src/a.txt', 'src/b.txt', 'src/c.md', 'src/sub'],
        },
        { shellForm: 'COPY s?c /app/', jsonForm: 'COPY ["s?c", "/app/"]', archivePaths: ['src'] },
        { shellForm: 'COPY {src,lib} /app/', jsonForm: 'COPY ["{src,lib}", "/app/"]', archivePaths: ['lib', 'src'] },
        {
          // A match inside a matched directory is left out, so nothing is uploaded twice
          shellForm: 'COPY src/** /app/',
          jsonForm: 'COPY ["src/**", "/app/"]',
          archivePaths: ['src/a.txt', 'src/b.txt', 'src/c.md', 'src/sub'],
        },
        {
          // A glob that matches nothing is skipped in either form
          shellForm: 'COPY src/*.cfg a.txt /app/',
          jsonForm: 'COPY ["src/*.cfg", "a.txt", "/app/"]',
          archivePaths: ['a.txt'],
        },
      ]

      for (const { shellForm, jsonForm, archivePaths } of cases) {
        const viaShell = Image.base('alpine').dockerfileCommands([shellForm], dir).contextList
        const viaJson = Image.base('alpine').dockerfileCommands([jsonForm], dir).contextList
        expect(viaShell.map((c) => c.archivePath).sort()).toEqual(archivePaths)
        expect(viaShell).toEqual(viaJson)

        fs.writeFileSync(path.join(dir, 'Dockerfile'), `FROM alpine\n${shellForm}\n`)
        const fromShell = Image.fromDockerfile(path.join(dir, 'Dockerfile')).contextList
        fs.writeFileSync(path.join(dir, 'Dockerfile'), `FROM alpine\n${jsonForm}\n`)
        const fromJson = Image.fromDockerfile(path.join(dir, 'Dockerfile')).contextList
        expect(fromShell.map((c) => c.archivePath).sort()).toEqual(archivePaths)
        expect(fromShell).toEqual(fromJson)
      }

      // A plain source that does not exist keeps its literal path, so the upload fails loudly
      expect(Image.base('alpine').dockerfileCommands(['COPY missing.txt /app/'], dir).contextList).toEqual([
        { sourcePath: path.join(dir, 'missing.txt'), archivePath: 'missing.txt' },
      ])
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('COPY globs outside the build context upload nothing from the host', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    try {
      for (const file of ['ctx/a.txt', 'outside/secret.txt', 'top.txt']) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }
      fs.symlinkSync('../outside/secret.txt', path.join(dir, 'ctx', 'link.txt'))
      const context = path.join(dir, 'ctx')

      // Unquoted, such a glob is dropped in every mode, as it was before shell-form globs were kept
      const outsideSources = [`${dir}/outside/*.txt`, '../*.txt', '../outside/*', 'src/../../*.txt', '..{,}/*']
      for (const source of outsideSources) {
        for (const options of [undefined, { strictContext: true }]) {
          expect(
            Image.base('alpine').dockerfileCommands([`COPY ${source} /app/`], context, options).contextList,
          ).toEqual([])
          expect(
            Image.base('alpine').dockerfileCommands([`COPY ${source} a.txt /app/`], context, options).contextList,
          ).toEqual([{ sourcePath: path.join(context, 'a.txt'), archivePath: 'a.txt' }])
        }
      }
      // With strictContext an unquoted match that resolves outside the context is dropped as well
      expect(
        Image.base('alpine').dockerfileCommands(['COPY *.txt /app/'], context, { strictContext: true }).contextList,
      ).toEqual([{ sourcePath: path.join(context, 'a.txt'), archivePath: 'a.txt' }])

      // Brace syntax matches outside the context in the quoted and JSON forms too; strictContext rejects it
      for (const line of ['COPY "..{,}/*" /app/', 'COPY ["..{,}/*", "/app/"]']) {
        expect(Image.base('alpine').dockerfileCommands([line], context).contextList).toEqual([])
        expect(() => Image.base('alpine').dockerfileCommands([line], context, { strictContext: true })).toThrow(
          'forbidden path outside the build context',
        )
      }
      for (const line of ['COPY "../*.txt" /app/', 'COPY ["link.txt", "/app/"]']) {
        expect(() => Image.base('alpine').dockerfileCommands([line], context, { strictContext: true })).toThrow(
          'forbidden path outside the build context',
        )
      }
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('COPY lines keep every build context entry they had before shell-form globs were kept', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    try {
      for (const file of [
        'top.txt',
        'ctx/a.txt',
        'ctx/package.json',
        'ctx/requirements.txt',
        'ctx/requirements-dev.txt',
        'ctx/src/a.txt',
        'ctx/src/sub/b.txt',
        'ctx/src/sub/deep/c.txt',
      ]) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }
      const context = path.join(dir, 'ctx')

      // before: the archive paths these lines gave before shell-form globs were kept
      const cases: Array<{ lines: string[]; options?: { strictContext: boolean }; before: string[]; after: string[] }> =
        [
          { lines: ['COPY package.json yarn.lock* ./'], before: ['package.json'], after: ['package.json'] },
          {
            lines: ['COPY requirements*.txt optional*.cfg /app/'],
            before: [],
            after: ['requirements-dev.txt', 'requirements.txt'],
          },
          { lines: ['ARG SRC=.', 'COPY $SRC /app/'], before: [''], after: [''] },
          {
            lines: ['COPY ../*.txt a.txt /app/'],
            options: { strictContext: true },
            before: ['a.txt'],
            after: ['a.txt'],
          },
          {
            lines: ['COPY /etc/*.conf a.txt /app/'],
            options: { strictContext: true },
            before: ['a.txt'],
            after: ['a.txt'],
          },
          {
            lines: ['COPY ..{,}/* a.txt /app/'],
            options: { strictContext: true },
            before: ['a.txt'],
            after: ['a.txt'],
          },
          { lines: ['COPY src/* /app/'], before: [], after: ['src/a.txt', 'src/sub'] },
          { lines: ['COPY src/** /app/'], before: [], after: ['src/a.txt', 'src/sub'] },
        ]

      for (const { lines, options, before, after } of cases) {
        fs.writeFileSync(path.join(context, 'Dockerfile'), `FROM alpine\n${lines.join('\n')}\n`)
        for (const contextList of [
          Image.base('alpine').dockerfileCommands(lines, context, options).contextList,
          Image.fromDockerfile(path.join(context, 'Dockerfile'), options).contextList,
        ]) {
          const archivePaths = contextList.map((c) => c.archivePath)
          expect(archivePaths).toEqual(expect.arrayContaining(before))
          for (const { sourcePath } of contextList) {
            expect(fs.existsSync(sourcePath)).toBe(true)
          }
          expect(archivePaths.sort()).toEqual(after)
        }
      }
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('COPY globs add only existing paths that resolve inside the build context', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    try {
      for (const file of [
        'top.txt',
        'outside/secret.txt',
        'ctx/a.txt',
        'ctx/src/b.txt',
        'ctx/lit*.txt',
        'ctx/{p,q}.txt',
      ]) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }
      fs.symlinkSync('../outside', path.join(dir, 'ctx', 'lnkout'))
      const context = path.join(dir, 'ctx')
      const archivePaths = (line: string, options?: { strictContext: boolean }): string[] =>
        Image.base('alpine')
          .dockerfileCommands([line], context, options)
          .contextList.map((c) => c.archivePath)
          .sort()

      for (const options of [undefined, { strictContext: true }]) {
        // An unquoted glob does not follow a symlink out of the context, with or without strictContext
        expect(archivePaths('COPY **/*.txt /app/', options)).toEqual(['a.txt', 'lit*.txt', 'src/b.txt', '{p,q}.txt'])
        expect(archivePaths('COPY lnkout/* a.txt /app/', options)).toEqual(['a.txt'])
        // The source before a glob destination was the destination before, so it adds nothing outside
        expect(archivePaths(`COPY ${dir}/top.txt dst*`, options)).toEqual([])
        expect(archivePaths('COPY a.txt dst*', options)).toEqual(['a.txt'])
        // A path below a file cannot exist, so a glob there matches nothing rather than throwing ENOTDIR
        expect(archivePaths('COPY a.txt/x* src/b.txt /app/', options)).toEqual(['src/b.txt'])
        expect(archivePaths('COPY "a.txt/x*" src/b.txt /app/', options)).toEqual(['src/b.txt'])
        expect(archivePaths('COPY ["a.txt/x*", "src/b.txt", "/app/"]', options)).toEqual(['src/b.txt'])
        expect(archivePaths('COPY a.txt/x dst*', options)).toEqual([])
        // A glob never uploads the context root, which tinyglobby gives for `..` in a brace group
        expect(archivePaths('COPY {a.txt,..} /app/', options)).toEqual([])
        expect(archivePaths('COPY ["{a.txt,..}", "/app/"]', options)).toEqual([])
      }

      // `.` and `./` still name the context root
      for (const [line, sourcePath] of [
        ['COPY . /app/', context],
        ['COPY ./ /app/', `${context}/`],
      ]) {
        expect(Image.base('alpine').dockerfileCommands([line], context).contextList).toEqual([
          { sourcePath, archivePath: '' },
        ])
      }

      // A file named like a glob is uploaded, in every form
      for (const line of ['COPY lit*.txt /app/', 'COPY "lit*.txt" /app/', 'COPY ["lit*.txt", "/app/"]']) {
        expect(archivePaths(line)).toEqual(['lit*.txt'])
      }
      for (const line of ['COPY {p,q}.txt /app/', 'COPY ["{p,q}.txt", "/app/"]']) {
        expect(archivePaths(line)).toEqual(['{p,q}.txt'])
      }
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('fromDockerfile resolves COPY globs against the real filesystem', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    try {
      for (const file of ['src/a.txt', 'src/b.txt', 'src/.env', 'conf/app.json']) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }
      const absoluteConf = path.join(dir, 'conf', '*.json')
      fs.writeFileSync(
        path.join(dir, 'Dockerfile'),
        `FROM alpine\nCOPY ["src/*", "/app/"]\nCOPY ["src", "/src/"]\nCOPY ["${absoluteConf}", "/conf/"]\n`,
      )

      const image = Image.fromDockerfile(path.join(dir, 'Dockerfile'))

      expect(image.contextList.map((c) => c.sourcePath).sort()).toEqual(
        ['conf/app.json', 'src', 'src/.env', 'src/a.txt', 'src/b.txt'].map((f) => path.join(dir, f)),
      )
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('dockerfileCommands archives glob matches relative to a relative context directory', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    const originalCwd = process.cwd()
    try {
      for (const file of ['ctx/a.txt', 'ctx/sub/c.txt']) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }
      process.chdir(dir)

      // The second form leaves the working directory and re-enters it.
      for (const contextDir of ['ctx', `../${path.basename(dir)}/ctx`]) {
        const image = Image.base('alpine').dockerfileCommands(
          ['COPY ["*.txt", "/app/"]', 'COPY ["sub/**", "/app/sub/"]', 'COPY ["{sub/c.txt,a.txt}", "/app/"]'],
          contextDir,
        )

        const a = { sourcePath: `${contextDir}/a.txt`, archivePath: 'a.txt' }
        const c = { sourcePath: `${contextDir}/sub/c.txt`, archivePath: 'sub/c.txt' }
        expect(image.contextList).toEqual([a, c, ...[a, c].sort((x, y) => x.sourcePath.localeCompare(y.sourcePath))])
      }
    } finally {
      process.chdir(originalCwd)
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('dockerfileCommands gives the same archive paths however the context directory is written', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    const originalCwd = process.cwd()
    try {
      for (const file of ['ctx/a.txt', 'ctx/b.txt', 'ctx/.env', 'ctx/sub/c.txt', 'ctx/ctx/d.txt']) {
        fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true })
        fs.writeFileSync(path.join(dir, file), '')
      }
      // Sources that start with '.' or with the context directory's own name are included on purpose
      const commands = [
        'COPY ["*.txt", "/app/"]',
        'COPY sub/c.txt /app/',
        'COPY sub/ /app/sub/',
        'COPY . /app/',
        'COPY ./ /app/',
        'COPY .env /app/',
        'COPY ctx/d.txt /app/',
      ]
      const archivePaths = ['', '', '.env', 'a.txt', 'b.txt', 'ctx/d.txt', 'sub/', 'sub/c.txt']

      // Each working directory is paired with the spellings of ctx that are relative to it
      const spellings: Array<[string, string[]]> = [
        [dir, ['ctx', 'ctx/', './ctx', './ctx/', path.join(dir, 'ctx')]],
        [path.join(dir, 'ctx'), ['.', './']],
      ]
      for (const [cwd, contextDirs] of spellings) {
        process.chdir(cwd)
        for (const contextDir of contextDirs) {
          const image = Image.base('alpine').dockerfileCommands(commands, contextDir)
          expect({ contextDir, archivePaths: image.contextList.map((c) => c.archivePath).sort() }).toEqual({
            contextDir,
            archivePaths,
          })
        }
      }
    } finally {
      process.chdir(originalCwd)
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('dockerfileCommands reads a leading !, bare parentheses and the context directory literally', async () => {
    const { Image } = await import('../Image')
    const fs = jest.requireActual<typeof import('fs')>('fs')
    const os = jest.requireActual<typeof import('os')>('os')
    const path = jest.requireActual<typeof import('path')>('path')
    const realModules: Record<string, unknown> = {
      fs,
      tinyglobby: jest.requireActual('tinyglobby'),
      'expand-tilde': jest.requireActual('expand-tilde'),
    }
    mockDynamicRequire.mockImplementation((moduleName: string) => realModules[moduleName])

    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-image-')))
    try {
      // An unbalanced parenthesis in the context path must not swallow the source's separators
      const contextDir = path.join(dir, 'proj(old', 'ctx')
      for (const file of [
        'a.txt',
        'sub/c.txt',
        'sub/!n.txt',
        '!d/x.txt',
        'app/(marketing)/page.tsx',
        'app/marketing/other.tsx',
      ]) {
        fs.mkdirSync(path.dirname(path.join(contextDir, file)), { recursive: true })
        fs.writeFileSync(path.join(contextDir, file), '')
      }

      const image = Image.base('alpine').dockerfileCommands(
        [
          'COPY ["sub/!*.txt", "/app/"]',
          'COPY ["!d/*.txt", "/app/"]',
          'COPY ["{sub/c.txt,a.txt}", "/app/"]',
          'COPY ["app/(marketing)/*", "/app/"]',
          'COPY ["sub/*/", "/app/"]',
        ],
        contextDir,
      )

      expect(image.contextList.map((c) => c.archivePath).sort()).toEqual([
        '!d/x.txt',
        'a.txt',
        'app/(marketing)/page.tsx',
        'sub/!n.txt',
        'sub/*/',
        'sub/c.txt',
      ])
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('splitGlobSegments and escapeLiteralParens tell glob groups from literal characters', async () => {
    const { Image } = await import('../Image')
    const split = (Image as unknown as Record<string, (pattern: string) => string[]>).splitGlobSegments

    expect(split('ctx/{sub/c.txt,a.txt}')).toEqual(['ctx', '{sub/c.txt,a.txt}'])
    expect(split('a/@(x/y|z)/c')).toEqual(['a', '@(x/y|z)', 'c'])
    expect(split('proj(old/sub/*.txt')).toEqual(['proj(old', 'sub', '*.txt'])
    expect(split('{app/(marketing)/page.tsx,a.txt}')).toEqual(['{app/(marketing)/page.tsx,a.txt}'])

    const escape = (Image as unknown as Record<string, (segment: string) => string>).escapeLiteralParens
    expect(escape('(marketing)')).toBe('\\(marketing\\)')
    expect(escape('@(a|b).txt')).toBe('@(a|b).txt')
    expect(escape('+(x(y))')).toBe('+(x\\(y\\))')
    expect(escape('a\\(b')).toBe('a\\(b')
    expect(split('/a\\{b/c')).toEqual(['', 'a\\{b', 'c'])
  })
})
