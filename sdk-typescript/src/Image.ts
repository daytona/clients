/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as pathe from 'pathe'
import { quote, parse as parseShellQuote } from 'shell-quote'
import { DaytonaInvalidArgumentError, DaytonaNotFoundError } from './errors/DaytonaError'
import { dynamicRequire } from './utils/Import'

const SUPPORTED_PYTHON_SERIES = ['3.9', '3.10', '3.11', '3.12', '3.13'] as const
type SupportedPythonSeries = (typeof SUPPORTED_PYTHON_SERIES)[number]
const LATEST_PYTHON_MICRO_VERSIONS = ['3.9.22', '3.10.17', '3.11.12', '3.12.10', '3.13.3']

/**
 * Represents a context file to be added to the image.
 *
 * @interface
 * @property {string} sourcePath - The path to the source file or directory.
 * @property {string} archivePath - The path inside the archive file in object storage.
 */
export interface Context {
  sourcePath: string
  archivePath: string
}

/**
 * Options for the pip install command.
 *
 * @interface
 * @property {string[]} findLinks - The find-links to use for the pip install command.
 * @property {string} indexUrl - The index URL to use for the pip install command.
 * @property {string[]} extraIndexUrls - The extra index URLs to use for the pip install command.
 * @property {boolean} pre - Whether to install pre-release versions.
 * @property {string} extraOptions - The extra options to use for the pip install command. Given string is passed directly to the pip install command.
 */
export interface PipInstallOptions {
  findLinks?: string[]
  indexUrl?: string
  extraIndexUrls?: string[]
  pre?: boolean
  extraOptions?: string
}

/**
 * Options for the pip install command from a pyproject.toml file.
 *
 * @interface
 * @property {string[]} optionalDependencies - The optional dependencies to install.
 *
 * @extends {PipInstallOptions}
 */
export interface PyprojectOptions extends PipInstallOptions {
  optionalDependencies?: string[]
}

/**
 * Represents an image definition for a Daytona sandbox.
 * Do not construct this class directly. Instead use one of its static factory methods,
 * such as `Image.base()`, `Image.debianSlim()` or `Image.fromDockerfile()`.
 *
 * @class
 * @property {string} dockerfile - The Dockerfile content.
 * @property {Context[]} contextList - The list of context files to be added to the image.
 */
export class Image {
  private _dockerfile = ''
  private _contextList: Context[] = []

  // eslint-disable-next-line @typescript-eslint/no-empty-function
  private constructor() {}

  get dockerfile(): string {
    return this._dockerfile
  }

  get contextList(): Context[] {
    return this._contextList
  }

  /**
   * Adds commands to install packages using pip.
   *
   * @param {string | string[]} packages - The packages to install.
   * @param {Object} options - The options for the pip install command.
   * @param {string[]} options.findLinks - The find-links to use for the pip install command.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image.debianSlim('3.12').pipInstall('numpy', { findLinks: ['https://pypi.org/simple'] })
   */
  pipInstall(packages: string | string[], options?: PipInstallOptions): Image {
    const pkgs = this.flattenStringArgs('pipInstall', 'packages', packages)
    if (!pkgs.length) return this

    const extraArgs = this.formatPipInstallArgs(options)
    this._dockerfile += `RUN python -m pip install ${quote(pkgs.sort())}${extraArgs}\n`

    return this
  }

  /**
   * Installs dependencies from a requirements.txt file.
   *
   * @param {string} requirementsTxt - The path to the requirements.txt file.
   * @param {PipInstallOptions} options - The options for the pip install command.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image.debianSlim('3.12')
   * image.pipInstallFromRequirements('requirements.txt', { findLinks: ['https://pypi.org/simple'] })
   */
  pipInstallFromRequirements(requirementsTxt: string, options?: PipInstallOptions): Image {
    const importErrorPrefix = '"pipInstallFromRequirements" is not supported: '
    const expandTilde = dynamicRequire('expand-tilde', importErrorPrefix)
    const fs = dynamicRequire('fs', importErrorPrefix)

    const expandedPath = expandTilde(requirementsTxt)
    if (!fs.existsSync(expandedPath)) {
      throw new DaytonaNotFoundError(`Requirements file ${requirementsTxt} does not exist`)
    }
    if (!fs.statSync(expandedPath).isFile()) {
      throw new DaytonaInvalidArgumentError(`Requirements path ${requirementsTxt} exists but is not a file`)
    }

    const extraArgs = this.formatPipInstallArgs(options)

    this._contextList.push({ sourcePath: expandedPath, archivePath: expandedPath })
    this._dockerfile += `COPY ${expandedPath} /.requirements.txt\n`
    this._dockerfile += `RUN python -m pip install -r /.requirements.txt${extraArgs}\n`

    return this
  }

  /**
   * Installs dependencies from a pyproject.toml file.
   *
   * @param {string} pyprojectToml - The path to the pyproject.toml file.
   * @param {PyprojectOptions} options - The options for the pip install command.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image.debianSlim('3.12')
   * image.pipInstallFromPyproject('pyproject.toml', { optionalDependencies: ['dev'] })
   */
  pipInstallFromPyproject(pyprojectToml: string, options?: PyprojectOptions): Image {
    const importErrorPrefix = '"pipInstallFromPyproject" is not supported: '
    const expandTilde = dynamicRequire('expand-tilde', importErrorPrefix)
    const toml = dynamicRequire('@iarna/toml', importErrorPrefix)
    const fs = dynamicRequire('fs', importErrorPrefix)
    const expandedPath = expandTilde(pyprojectToml)

    if (!fs.existsSync(expandedPath)) {
      throw new DaytonaNotFoundError(`pyproject.toml file ${pyprojectToml} does not exist`)
    }
    if (!fs.statSync(expandedPath).isFile()) {
      throw new DaytonaInvalidArgumentError(`pyproject.toml path ${pyprojectToml} exists but is not a file`)
    }

    let tomlData: any

    try {
      tomlData = toml.parse(fs.readFileSync(expandedPath, 'utf-8')) as any
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error)
      throw new DaytonaInvalidArgumentError(`Invalid pyproject.toml file ${pyprojectToml}: ${errorMessage}`)
    }

    const dependencies: string[] = []

    if (!tomlData || !tomlData.project || !Array.isArray(tomlData.project.dependencies)) {
      const msg =
        'No [project.dependencies] section in pyproject.toml file. ' +
        'See https://packaging.python.org/en/latest/guides/writing-pyproject-toml ' +
        'for further file format guidelines.'
      throw new DaytonaInvalidArgumentError(msg)
    }

    dependencies.push(...tomlData.project.dependencies)

    if (options?.optionalDependencies && tomlData.project['optional-dependencies']) {
      const optionalGroups = tomlData.project['optional-dependencies']
      if (typeof optionalGroups !== 'object' || Array.isArray(optionalGroups)) {
        throw new DaytonaInvalidArgumentError('optional-dependencies must be a mapping in pyproject.toml')
      }

      for (const group of options.optionalDependencies) {
        const deps = (optionalGroups as Record<string, string[]>)[group]
        if (Array.isArray(deps)) {
          dependencies.push(...deps)
        }
      }
    }

    return this.pipInstall(dependencies, options)
  }

  /**
   * Adds a local file to the image.
   *
   * @param {string} localPath - The path to the local file.
   * @param {string} remotePath - The path of the file in the image.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .addLocalFile('requirements.txt', '/home/daytona/requirements.txt')
   */
  addLocalFile(localPath: string, remotePath: string): Image {
    const importErrorPrefix = '"addLocalFile" is not supported: '
    const expandTilde = dynamicRequire('expand-tilde', importErrorPrefix)
    const fs = dynamicRequire('fs', importErrorPrefix)

    if (remotePath.endsWith('/')) {
      remotePath = remotePath + pathe.basename(localPath)
    }

    const expandedPath = expandTilde(localPath)
    if (!fs.existsSync(expandedPath)) {
      throw new DaytonaNotFoundError(`Local file ${localPath} does not exist`)
    }
    if (!fs.statSync(expandedPath).isFile()) {
      throw new DaytonaInvalidArgumentError(`Local path ${localPath} exists but is not a file`)
    }

    this._contextList.push({ sourcePath: expandedPath, archivePath: expandedPath })
    this._dockerfile += `COPY ${expandedPath} ${remotePath}\n`

    return this
  }

  /**
   * Adds a local directory to the image.
   *
   * @param {string} localPath - The path to the local directory.
   * @param {string} remotePath - The path of the directory in the image.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .addLocalDir('src', '/home/daytona/src')
   */
  addLocalDir(localPath: string, remotePath: string): Image {
    const importErrorPrefix = '"addLocalDir" is not supported: '
    const expandTilde = dynamicRequire('expand-tilde', importErrorPrefix)
    const fs = dynamicRequire('fs', importErrorPrefix)

    const expandedPath = expandTilde(localPath)
    if (!fs.existsSync(expandedPath)) {
      throw new DaytonaNotFoundError(`Local directory ${localPath} does not exist`)
    }
    if (!fs.statSync(expandedPath).isDirectory()) {
      throw new DaytonaInvalidArgumentError(`Local path ${localPath} exists but is not a directory`)
    }

    this._contextList.push({ sourcePath: expandedPath, archivePath: expandedPath })
    this._dockerfile += `COPY ${expandedPath} ${remotePath}\n`

    return this
  }

  /**
   * Runs commands in the image.
   *
   * @param {string | string[]} commands - The commands to run.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .runCommands(
   *    'echo "Hello, world!"',
   *    ['bash', '-c', 'echo Hello, world, again!']
   *  )
   */
  runCommands(...commands: (string | string[])[]): Image {
    for (const command of commands) {
      if (Array.isArray(command)) {
        this._dockerfile += `RUN ${command.map((c) => `"${c.replace(/"/g, '\\\\\\"').replace(/'/g, "\\'")}"`).join(' ')}\n`
      } else {
        this._dockerfile += `RUN ${command}\n`
      }
    }

    return this
  }

  /**
   * Sets environment variables in the image.
   *
   * @param {Record<string, string>} envVars - The environment variables to set.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .env({ FOO: 'bar' })
   */
  env(envVars: Record<string, string>): Image {
    const nonStringKeys = Object.entries(envVars)
      .filter(([, value]) => typeof value !== 'string')
      .map(([key]) => key)

    if (nonStringKeys.length) {
      throw new DaytonaInvalidArgumentError(`Image ENV variables must be strings. Invalid keys: ${nonStringKeys}`)
    }

    for (const [key, val] of Object.entries(envVars)) {
      this._dockerfile += `ENV ${key}=${quote([val])}\n`
    }

    return this
  }

  /**
   * Sets the working directory in the image.
   *
   * @param {string} dirPath - The path to the working directory.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .workdir('/home/daytona')
   */
  workdir(dirPath: string): Image {
    this._dockerfile += `WORKDIR ${quote([dirPath])}\n`
    return this
  }

  /**
   * Sets the entrypoint for the image.
   *
   * @param {string[]} entrypointCommands - The commands to set as the entrypoint.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .entrypoint(['/bin/bash'])
   */
  entrypoint(entrypointCommands: string[]): Image {
    if (!Array.isArray(entrypointCommands) || !entrypointCommands.every((x) => typeof x === 'string')) {
      throw new DaytonaInvalidArgumentError('entrypoint_commands must be a list of strings')
    }

    const argsStr = entrypointCommands.map((arg) => `"${arg}"`).join(', ')
    this._dockerfile += `ENTRYPOINT [${argsStr}]\n`

    return this
  }

  /**
   * Sets the default command for the image.
   *
   * @param {string[]} cmd - The command to set as the default command.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .cmd(['/bin/bash'])
   */
  cmd(cmd: string[]): Image {
    if (!Array.isArray(cmd) || !cmd.every((x) => typeof x === 'string')) {
      throw new DaytonaInvalidArgumentError('Image CMD must be a list of strings')
    }

    const cmdStr = cmd.map((arg) => `"${arg}"`).join(', ')
    this._dockerfile += `CMD [${cmdStr}]\n`

    return this
  }

  /**
   * Extends an image with arbitrary Dockerfile-like commands.
   *
   * @param {string | string[]} dockerfileCommands - The commands to add to the Dockerfile.
   * @param {string} contextDir - The path to the context directory.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image
   *  .debianSlim('3.12')
   *  .dockerfileCommands(['RUN echo "Hello, world!"'])
   */
  dockerfileCommands(dockerfileCommands: string[], contextDir?: string, options?: { strictContext?: boolean }): Image {
    const strictContext = options?.strictContext ?? false
    if (!contextDir && strictContext) {
      throw new DaytonaInvalidArgumentError(
        'strictContext requires contextDir so that the build context boundary is explicit',
      )
    }
    if (contextDir) {
      const importErrorPrefix = '"dockerfileCommands" is not supported: '
      const expandTilde = dynamicRequire('expand-tilde', importErrorPrefix)
      const fs = dynamicRequire('fs', importErrorPrefix)

      const expandedPath = expandTilde(contextDir)
      if (!fs.existsSync(expandedPath)) {
        throw new DaytonaNotFoundError(`Context directory ${contextDir} does not exist`)
      }
      if (!fs.statSync(expandedPath).isDirectory()) {
        throw new DaytonaInvalidArgumentError(`Context path ${contextDir} exists but is not a directory`)
      }
      if (strictContext) {
        // Resolve sources against the directory that was validated, not the literal argument
        contextDir = expandedPath
      }
    }

    // Source paths are joined onto contextDir, which normalises it ('./ctx' becomes 'ctx' and '.'
    // disappears), so archive paths are cut by the normalised directory, not the raw argument
    const normalizedContextDir = contextDir ? pathe.join(contextDir, '.') : ''
    const contextPrefixLength = normalizedContextDir === '.' ? 0 : normalizedContextDir.length

    for (const [contextPath, originalPath] of Image.extractCopySources(
      dockerfileCommands.join('\n'),
      contextDir || '',
      strictContext,
    )) {
      let archiveBasePath = contextPath
      // Absolute sources inside the context keep their full archive path, as before; the others are
      // cut by the context directory
      if (contextDir && !(pathe.isAbsolute(originalPath) && originalPath.startsWith(contextDir))) {
        archiveBasePath = contextPath.substring(contextPrefixLength)
        // Remove leading separators, and give the context root ('.' or './' once joined) an empty path
        // eslint-disable-next-line no-useless-escape
        archiveBasePath = archiveBasePath.replace(/^[\/\\]+/, '').replace(/^\.\/?$/, '')
      }
      this._contextList.push({ sourcePath: contextPath, archivePath: archiveBasePath })
    }

    this._dockerfile += dockerfileCommands.join('\n') + '\n'
    return this
  }

  /**
   * Creates an Image from an existing Dockerfile.
   *
   * @param {string} path - The path to the Dockerfile.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image.fromDockerfile('Dockerfile')
   */
  static fromDockerfile(path: string, options?: { strictContext?: boolean }): Image {
    const importErrorPrefix = '"fromDockerfile" is not supported: '
    const expandTilde = dynamicRequire('expand-tilde', importErrorPrefix)
    const fs = dynamicRequire('fs', importErrorPrefix)

    const expandedPath = pathe.resolve(expandTilde(path))
    if (!fs.existsSync(expandedPath)) {
      throw new DaytonaNotFoundError(`Dockerfile ${path} does not exist`)
    }
    if (!fs.statSync(expandedPath).isFile()) {
      throw new DaytonaInvalidArgumentError(`Dockerfile path ${path} exists but is not a file`)
    }

    const dockerfileContent = fs.readFileSync(expandedPath, 'utf-8')
    const img = new Image()
    img._dockerfile = dockerfileContent

    // Remove dockerfile filename from path to get the path prefix
    const pathPrefix = pathe.dirname(expandedPath) + pathe.sep

    for (const [contextPath, originalPath] of Image.extractCopySources(
      dockerfileContent,
      pathPrefix,
      options?.strictContext ?? false,
    )) {
      let archiveBasePath = contextPath
      if (!originalPath.startsWith(pathPrefix)) {
        // Remove the path prefix from the context path to get the archive path
        archiveBasePath = contextPath.substring(pathPrefix.length)
        // Remove leading separators
        // eslint-disable-next-line no-useless-escape
        archiveBasePath = archiveBasePath.replace(/^[\/\\]+/, '')
      }
      img._contextList.push({ sourcePath: contextPath, archivePath: archiveBasePath })
    }

    return img
  }

  /**
   * Creates an Image from an existing base image.
   *
   * @param {string} image - The base image to use.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image.base('python:3.12-slim-bookworm')
   */
  static base(image: string): Image {
    const img = new Image()
    img._dockerfile = `FROM ${image}\n`
    return img
  }

  /**
   * Creates a Debian slim image based on the official Python Docker image.
   *
   * @param {string} pythonVersion - The Python version to use.
   * @returns {Image} The Image instance.
   *
   * @example
   * const image = Image.debianSlim('3.12')
   */
  static debianSlim(pythonVersion?: SupportedPythonSeries): Image {
    const version = Image.processPythonVersion(pythonVersion)
    const img = new Image()

    const commands = [
      `FROM python:${version}-slim-bookworm`,
      'RUN apt-get update',
      'RUN apt-get install -y gcc gfortran build-essential',
      'RUN pip install --upgrade pip',
      // Set debian front-end to non-interactive to avoid users getting stuck with input prompts.

      "RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections",
    ]

    img._dockerfile = commands.join('\n') + '\n'
    return img
  }

  /**
   * Formats pip install arguments in a single string.
   *
   * @param {PipInstallOptions} options - The options for the pip install command.
   * @returns {string} The formatted pip install arguments.
   */
  private formatPipInstallArgs(options?: PipInstallOptions): string {
    if (!options) return ''

    let extraArgs = ''

    if (options.findLinks) {
      for (const findLink of options.findLinks) {
        extraArgs += ` --find-links ${quote([findLink])}`
      }
    }

    if (options.indexUrl) {
      extraArgs += ` --index-url ${quote([options.indexUrl])}`
    }

    if (options.extraIndexUrls) {
      for (const extraIndexUrl of options.extraIndexUrls) {
        extraArgs += ` --extra-index-url ${quote([extraIndexUrl])}`
      }
    }

    if (options.pre) {
      extraArgs += ' --pre'
    }

    if (options.extraOptions) {
      extraArgs += ` ${options.extraOptions.trim()}`
    }

    return extraArgs
  }

  /**
   * Flattens a string argument.
   *
   * @param {string} functionName - The name of the function.
   * @param {string} argName - The name of the argument.
   * @param {any} args - The argument to flatten.
   * @returns {string[]} The flattened argument.
   */
  private flattenStringArgs(functionName: string, argName: string, args: any): string[] {
    const result: string[] = []

    const flatten = (arg: any) => {
      if (typeof arg === 'string') {
        result.push(arg)
      } else if (Array.isArray(arg)) {
        for (const item of arg) {
          flatten(item)
        }
      } else {
        throw new DaytonaInvalidArgumentError(`${functionName}: ${argName} must only contain strings`)
      }
    }

    flatten(args)
    return result
  }

  /**
   * Processes the Python version.
   *
   * @param {string} pythonVersion - The Python version to use.
   * @returns {string} The processed Python version.
   */
  private static processPythonVersion(pythonVersion?: SupportedPythonSeries): string {
    if (!pythonVersion) {
      // Default to latest
      pythonVersion = SUPPORTED_PYTHON_SERIES[SUPPORTED_PYTHON_SERIES.length - 1]
    }

    if (!SUPPORTED_PYTHON_SERIES.includes(pythonVersion)) {
      throw new DaytonaInvalidArgumentError(
        `Unsupported Python version: ${pythonVersion}. ` +
          `Daytona supports the following series: ${SUPPORTED_PYTHON_SERIES.join(', ')}`,
      )
    }

    // Map series to latest micro version
    const seriesMap = Object.fromEntries(
      LATEST_PYTHON_MICRO_VERSIONS.map((v) => {
        const [major, minor, micro] = v.split('.')
        return [`${major}.${minor}`, micro]
      }),
    )

    const micro = seriesMap[pythonVersion]
    return `${pythonVersion}.${micro}`
  }

  /**
   * Joins backslash-continued physical lines into logical Dockerfile instruction lines.
   *
   * @param {string} dockerfileContent - The content of the Dockerfile.
   * @returns {string[]} The logical instruction lines.
   */
  private static dockerfileLogicalLines(dockerfileContent: string): string[] {
    const logicalLines: string[] = []
    let current: string | null = null

    for (const physicalLine of dockerfileContent.split(/\r?\n/)) {
      const isComment = physicalLine.trimStart().startsWith('#')
      if (current !== null && (!physicalLine.trim() || isComment)) {
        // Docker drops empty and comment lines that appear inside a continued instruction
        continue
      }
      const stripped = physicalLine.trimEnd()
      // A trailing backslash on a comment line is literal; comments never continue onto the next line
      const continued = !isComment && stripped.endsWith('\\')
      const segment = continued ? stripped.slice(0, -1) : physicalLine
      current = current === null ? segment : current + segment
      if (!continued) {
        logicalLines.push(current)
        current = null
      }
    }

    if (current !== null) {
      logicalLines.push(current)
    }

    return logicalLines
  }

  /**
   * Extracts source files from COPY commands in a Dockerfile.
   *
   * @param {string} dockerfileContent - The content of the Dockerfile.
   * @param {string} pathPrefix - The path prefix to use for the sources.
   * @returns {Array<[string, string]>} The list of the actual file path and its corresponding COPY-command source path.
   */
  private static extractCopySources(
    dockerfileContent: string,
    pathPrefix = '',
    strictContext = false,
  ): Array<[string, string]> {
    const sources: Array<[string, string]> = []
    const lines = Image.dockerfileLogicalLines(dockerfileContent)

    for (const line of lines) {
      // Skip empty lines and comments
      if (!line.trim() || line.trim().startsWith('#')) {
        continue
      }

      // Check if the line contains a COPY command
      if (/^\s*COPY\s+(?!.*--from=)/i.test(line)) {
        // Skip COPY instructions that use heredoc syntax (inline content, not file references)
        if (line.includes('<<')) {
          continue
        }

        const importErrorPrefix = '"extractCopySources" is not supported: '
        const tinyglobby = dynamicRequire('tinyglobby', importErrorPrefix)
        const fs = dynamicRequire('fs', importErrorPrefix)
        const realRoot = strictContext ? Image.resolveRealPath(fs, pathPrefix) : null
        let optionalRoot = realRoot

        const commandParts = this.parseCopyCommand(line)
        if (commandParts) {
          // Get source paths from the parsed command parts
          for (const [index, source] of commandParts.sources.entries()) {
            if (realRoot !== null) {
              Image.ensureSourceWithinContext(source)
            }

            // Handle absolute and relative paths differently
            const fullPathPattern = pathe.isAbsolute(source) ? source : pathe.join(pathPrefix, source)

            const matches = Image.expandCopySource(tinyglobby, pathPrefix, source)
            // An optional source only adds paths that exist and resolve inside the build context, in
            // every mode: it never throws and never adds a literal path that does not exist
            const optional = commandParts.optionalSources?.has(index) ?? false
            let candidates: string[]
            if (matches === null && !optional) {
              candidates = [fullPathPattern]
            } else if (matches !== null && matches.length > 0) {
              candidates = matches
            } else {
              // A glob that matches nothing is skipped, as BuildKit does, unless a path with its
              // literal name exists, which is what was uploaded before
              if (!Image.pathExists(fs, fullPathPattern)) continue
              candidates = [fullPathPattern]
            }

            for (const candidate of candidates) {
              const relative = pathe.relative(pathPrefix, candidate)
              // tinyglobby gives the context root for `..` in a brace group (`{a.txt,..}`). Only a
              // literal source such as `.` or `./` names the root, so a glob match of it is left out.
              if (matches !== null && relative === '') continue
              // Brace syntax, as in `..{,}/*`, lets a source inside the context match outside it
              const escapes = !Image.isOutsideContext(source) && Image.isOutsideContext(relative)
              if (optional) {
                // A symlink can lead outside the context, so the resolved path is checked without
                // strictContext too
                optionalRoot ??= Image.resolveRealPath(fs, pathPrefix)
                if (escapes || Image.findOutsideBuildContext(fs, optionalRoot, candidate) !== null) continue
              } else {
                // strictContext rejects a quoted or JSON source that resolves outside the context; without
                // it, only the brace matches outside the context are left out
                Image.ensureWithinBuildContext(fs, realRoot, candidate)
                if (escapes) continue
              }
              sources.push([candidate, source])
            }
          }
        }
      }
    }

    return sources
  }

  /**
   * Tells whether a path exists, without following a final symlink. lstat also throws for a path
   * that cannot exist, such as ENOTDIR when a parent is a file, so any error means it does not.
   *
   * @param {any} fs - The filesystem module.
   * @param {string} target - The path to check.
   * @returns {boolean} True if the path exists.
   */
  private static pathExists(fs: any, target: string): boolean {
    try {
      return fs.lstatSync(target, { throwIfNoEntry: false }) !== undefined
    } catch {
      return false
    }
  }

  /**
   * Resolves a path to its real location, falling back to a lexical absolute path when it cannot
   * be resolved.
   *
   * @param {any} fs - The filesystem module.
   * @param {string} target - The path to resolve.
   * @returns {string} The resolved path.
   */
  private static resolveRealPath(fs: any, target: string): string {
    try {
      return pathe.normalize(fs.realpathSync(target))
    } catch {
      return pathe.resolve(target)
    }
  }

  /**
   * Rejects a COPY source that names something outside the build context.
   *
   * @param {string} source - The COPY-command source path.
   */
  private static ensureSourceWithinContext(source: string): void {
    if (Image.isOutsideContext(source)) {
      throw new DaytonaInvalidArgumentError(`forbidden path outside the build context: ${source}`)
    }
  }

  /**
   * Tells whether a COPY source names something outside the build context. This is decided on the
   * source itself, before anything is read, so that a source is judged the same way whether or not
   * it happens to exist on disk. An absolute source, including a Windows drive or UNC path, and one
   * whose normalised form climbs above the context both name something the build context cannot
   * address.
   *
   * @param {string} source - The COPY-command source path.
   * @returns {boolean} True if the source is outside the build context.
   */
  private static isOutsideContext(source: string): boolean {
    const normalized = pathe.normalize(source)
    return (
      pathe.isAbsolute(source) ||
      /^([A-Za-z]:|\\\\)/.test(source) ||
      normalized === '..' ||
      normalized.startsWith('../')
    )
  }

  /**
   * Rejects a source that resolves outside the build context. Normalisation alone cannot see
   * this: a symlinked parent directory is traversed transparently by the archiver, so a regular
   * file reached through one is stored with its contents even though the written path stays
   * inside the context.
   *
   * @param {any} fs - The filesystem module.
   * @param {string | null} realRoot - The resolved build context root, or null when the caller did
   * not ask for a strict build context.
   * @param {string} candidate - The resolved source path to check.
   */
  private static ensureWithinBuildContext(fs: any, realRoot: string | null, candidate: string): void {
    const outside = Image.findOutsideBuildContext(fs, realRoot, candidate)
    if (outside !== null) {
      throw new DaytonaInvalidArgumentError(`forbidden path outside the build context: ${outside}`)
    }
  }

  /**
   * Finds where a source resolves outside the build context, as checked by ensureWithinBuildContext.
   *
   * @param {any} fs - The filesystem module.
   * @param {string | null} realRoot - The resolved build context root, or null when the caller did
   * not ask for a strict build context.
   * @param {string} candidate - The resolved source path to check.
   * @returns {string | null} The path outside the build context, or null if there is none.
   */
  private static findOutsideBuildContext(fs: any, realRoot: string | null, candidate: string): string | null {
    // realRoot is null unless the caller asked for a strict build context
    if (realRoot === null) return null

    let resolved: string
    try {
      resolved = pathe.normalize(fs.realpathSync(candidate))
    } catch {
      // A path that does not exist cannot be archived. A symlink whose target is missing still
      // leaves the context, so an entry that lstat can see is rejected rather than tolerated.
      return fs.lstatSync(candidate, { throwIfNoEntry: false }) ? candidate : null
    }

    const relative = pathe.relative(realRoot, resolved)
    return relative === '..' || relative.startsWith('../') ? resolved : null
  }

  /**
   * Parses a COPY command to extract sources and destination.
   *
   * @param {string} line - The line to parse.
   * @returns {Object} The parsed sources and destination, and the indexes of the optional sources,
   * which the shell form read only once its glob tokens were kept, if there are any.
   */
  private static parseCopyCommand(
    line: string,
  ): { sources: string[]; dest: string; optionalSources?: Set<number> } | null {
    // Remove initial "COPY" and strip whitespace
    let parts = line.trim().substring(4).trim()

    // Skip leading flags. Value-taking flags use the --flag=value form (--chown=..., --chmod=...)
    // and boolean flags stand alone (--link), so a flag never consumes the token that follows it.
    while (parts.startsWith('--')) {
      parts = parts.replace(/^\S+\s*/, '')
    }

    // Handle JSON array format: COPY ["src1", "src2", "dest"]
    if (parts.startsWith('[')) {
      let decoded: unknown
      try {
        decoded = JSON.parse(parts)
      } catch {
        return null
      }

      if (!Array.isArray(decoded) || !decoded.every((element): element is string => typeof element === 'string')) {
        return null
      }

      if (decoded.length < 2) {
        return null
      }

      return {
        sources: decoded.slice(0, -1),
        dest: decoded[decoded.length - 1],
      }
    }

    // Handle the whitespace-separated format. shell-quote returns an unquoted `*` or `?` source as a
    // glob token; keep its pattern so it expands like the JSON form.
    const tokens = parseShellQuote(parts)
    // A variable is replaced with an empty string, so `$SRC/*` would become `/*`. Other operators,
    // such as the parentheses in `src/(group)/*`, split a word into pieces that are not Docker's
    // words (`/*` alone would expand from /). Glob tokens are kept only without either, and the other
    // lines keep only the string tokens, as before.
    const keepGlobs = !parts.includes('$') && !tokens.some((x) => typeof x === 'object' && 'op' in x && x.op !== 'glob')
    const words = tokens.flatMap((x) => {
      if (typeof x === 'string') return [{ word: x, glob: false }]
      return keepGlobs && 'op' in x && x.op === 'glob' ? [{ word: x.pattern, glob: true }] : []
    })

    // We need at least one source and one destination
    if (words.length < 2) {
      return null
    }

    // The sources that were dropped while glob tokens were dropped are optional: the glob sources, and
    // the last string when the destination is a glob, as that string used to be the destination
    const lastString = words.map(({ glob }) => glob).lastIndexOf(false)
    const sourceWords = words.slice(0, -1).map(({ word, glob }, index) => ({
      word,
      optional: glob || (words[words.length - 1].glob && index === lastString),
    }))
    // An optional source outside the build context, such as `/etc/*` or `../*`, is dropped in every
    // mode, as before, so the shell form never reads host paths outside the context
    const kept = sourceWords.filter(({ word, optional }) => !optional || !Image.isOutsideContext(word))
    if (kept.length === 0) {
      return null
    }

    const optionalSources = new Set(kept.flatMap(({ optional }, index) => (optional ? [index] : [])))
    return {
      sources: kept.map(({ word }) => word),
      dest: words[words.length - 1].word,
      ...(optionalSources.size > 0 && { optionalSources }),
    }
  }

  /**
   * Expands a COPY source glob. Only the source is parsed as a glob; the context directory is
   * taken literally, as Docker does. Matches are prefixed with the joined context directory, which
   * callers slice to get archive paths.
   *
   * @param {any} tinyglobby - The tinyglobby module.
   * @param {string} pathPrefix - The context directory prefix.
   * @param {string} source - The COPY source.
   * @returns {string[] | null} The matching files and directories, which may be none, or null for a
   * source that is taken literally.
   */
  private static expandCopySource(tinyglobby: any, pathPrefix: string, source: string): string[] | null {
    const absolute = pathe.isAbsolute(source)
    const normalized = absolute ? source : pathe.normalize(source)
    // A glob with a trailing separator is taken literally, as before, since tinyglobby would match the
    // files next to the directories it names as well
    if (normalized.endsWith('/')) return null
    const segments = Image.splitGlobSegments(normalized)
    const globSegments = segments.map((segment) => Image.escapeLiteralParens(segment))
    const baseLength = globSegments.findIndex((segment) => tinyglobby.isDynamicPattern(segment))
    if (baseLength === -1) return null

    const staticPart = segments.slice(0, baseLength).join('/')
    const base = absolute ? staticPart || '/' : pathe.join(pathPrefix, staticPart)
    let pattern = globSegments.slice(baseLength).join('/')
    // A leading `!` would make tinyglobby read the pattern as a negation; Docker reads it literally
    if (pattern.startsWith('!') && !pattern.startsWith('!(')) pattern = `\\${pattern}`

    // Directories match too, as in Docker, and tinyglobby marks them with a trailing separator. A
    // source outside the build context still matches only files, so `/*` never reads host directories.
    const matches: string[] = tinyglobby.globSync([pattern], {
      cwd: base,
      dot: true,
      expandDirectories: false,
      onlyFiles: Image.isOutsideContext(source),
    })
    const directories = new Set(matches.filter((match) => match.endsWith('/')).map((match) => match.slice(0, -1)))
    // A directory is uploaded with everything below it, so a match inside a matched directory, as
    // with `**`, is left out rather than uploaded twice
    const insideMatchedDirectory = (match: string): boolean => {
      for (let parent = pathe.dirname(match); parent !== '.' && parent !== '/'; parent = pathe.dirname(parent)) {
        if (directories.has(parent)) return true
      }
      return false
    }
    return matches
      .map((match) => (match.endsWith('/') ? match.slice(0, -1) : match))
      .filter((match) => !insideMatchedDirectory(match))
      .map((match) => pathe.join(base, match))
  }

  /**
   * Escapes parentheses that do not open an extglob group (`@(`, `!(`, `?(`, `+(`, `*(`), so a
   * directory such as Next.js's `(marketing)` matches literally, as Docker reads it.
   *
   * @param {string} segment - A glob path segment.
   * @returns {string} The segment with its literal parentheses escaped.
   */
  private static escapeLiteralParens(segment: string): string {
    let escaped = ''
    const groups: boolean[] = []
    for (let i = 0; i < segment.length; i++) {
      const char = segment[i]
      if (char === '\\') {
        escaped += char + (segment[i + 1] ?? '')
        i++
      } else if (char === '(') {
        const extglob = i > 0 && '@!?+*'.includes(segment[i - 1])
        groups.push(extglob)
        escaped += extglob ? char : '\\('
      } else if (char === ')' && groups.length > 0) {
        escaped += groups.pop() ? char : '\\)'
      } else if (char === ')') {
        escaped += '\\)'
      } else {
        escaped += char
      }
    }
    return escaped
  }

  /**
   * Splits a glob pattern on the `/` separators outside brace and extglob groups, so a group
   * such as `{sub/a.txt,b.txt}` stays one segment.
   *
   * @param {string} pattern - The glob pattern.
   * @returns {string[]} The pattern's path segments.
   */
  private static splitGlobSegments(pattern: string): string[] {
    const segments: string[] = []
    let braces = 0
    let parens = 0
    let start = 0
    for (let i = 0; i < pattern.length; i++) {
      const char = pattern[i]
      if (char === '\\') i++
      else if (char === '{') braces++
      else if (char === '}' && braces > 0) braces--
      else if (char === '(' && i > 0 && '@!?+*'.includes(pattern[i - 1])) parens++
      else if (char === ')' && parens > 0) parens--
      else if (char === '/' && braces === 0 && parens === 0) {
        segments.push(pattern.slice(start, i))
        start = i + 1
      }
    }
    segments.push(pattern.slice(start))
    return segments
  }
}
