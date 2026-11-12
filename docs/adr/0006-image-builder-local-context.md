# 0006. Local files in the image builder

Status: implemented

## Context

The 0.x SDKs described an image with `Image`: a base (`Image.base`,
`Image.debian_slim`, `Image.from_dockerfile(path)`), layers (`pip_install`,
`run_commands`, `env`, `workdir`, `cmd`, `entrypoint`, `dockerfile_commands`),
and local files (`add_local_file`, `add_local_dir`,
`pip_install_from_requirements`, `pip_install_from_pyproject`). Local files
were uploaded with the image definition and copied into it.

The 2.x `ImageBuilder` started without local files: it renders a Dockerfile
that `POST /images` builds with no build context, so a `COPY` of a local path
has nothing to read. Code that ported `Image.debian_slim().add_local_dir("src",
...)` had to write its own Dockerfile and tar archive. The API now accepts a
build context: a tar archive declared with `POST /build-contexts`, uploaded in
parts, verified with `POST /build-contexts/{id}/complete`, and named by
`POST /images` with `context_upload_id`. The SDKs expose it as `images.build_from_context()` /
`images.buildFromContext()`, accepting archive bytes, a tar-file path or a
directory path.

This record decides how the builder carries local files, how a directory
becomes a build context, and which 0.x helpers 2.x keeps.

## Decision

### The builder carries a context

`ImageBuilder` gains, in both SDKs:

| Python | TypeScript | Renders |
|---|---|---|
| `add_local_file(local_path, remote_path)` | `addLocalFile(localPath, remotePath)` | `COPY ["<archive path>", "<remote path>"]` |
| `add_local_dir(local_path, remote_path)` | `addLocalDir(localPath, remotePath)` | `COPY ["<archive path>", "<remote path>"]` |
| `pip_install_from_requirements(requirements_txt)` | `pipInstallFromRequirements(path)` | `COPY ["<archive path>", "/.requirements.txt"]` then `RUN python -m pip install -r /.requirements.txt` |
| `debian_slim(python_version=None)` | `debianSlim(pythonVersion?)` | the 0.x `debian_slim` base (below) |
| `user(name)`, `cmd(*args)`, `entrypoint(*args)` | `user(name)`, `cmd(...args)`, `entrypoint(...args)` | `USER name`, `CMD [...]`, `ENTRYPOINT [...]` |

Python's required leading arguments are positional-only, including the local
paths, remote paths and `build_from_context(context, /)`. The optional
`debian_slim(python_version=None)` remains callable by keyword.

- **Paths are checked when the method is called.** Only `~` and `~/` (also
  `~\` on Windows) expand to the current user's home; `~user` stays literal.
  The path is made absolute. Blank paths and non-string values are refused
  (Python also accepts `os.PathLike[str]`). Local-path validation messages
  name the argument `localPath` in both SDKs, including Python's `local_path`
  and `requirements_txt`, so the message is shared. NUL characters are refused
  with `ImageBuilder localPath must not contain a NUL character`. A path that
  does not exist, or is not a file
  (`add_local_file`, `pip_install_from_requirements`) or a directory
  (`add_local_dir`), is a `BadRequestError`. The files are read when the
   image is built, not when the method is called. A declaration-time stat that
   fails with `EACCES` or `EPERM` raises `build context file is not readable: "<path>"`.
- **Remote paths** are single Dockerfile lines (the rule `workdir` follows)
  and, like `workdir` and the 0.x builder, keep Dockerfile variable
  substitution: `/opt/$APP` names the directory the image's `APP` variable
  holds. For `add_local_file`, a remote path ending in `/` gets the file's
  name appended, as in 0.x; that name is the file's own, so `\`, `$`, `'` and
  `"` in it are escaped with a backslash and a file named `a$b.txt` or
  `it's.txt` lands under that name.
- **Archive paths are positional.** The *n*-th local source of a builder
  (counting from 1, across all three methods) is archived under
  `.daytona-context/<n>/`. A file keeps its basename when that name is valid
  UTF-8 and contains no `$`, `\`, `*`, `?`, `[`, `]`, `'` or `"`, or control characters
  (C0, DEL or C1): for example, `.daytona-context/2/config.json`. This preserves
  the basename when the remote path names an existing directory, even without
  a trailing `/`. Other UTF-8 file names use the fixed name `file`, avoiding
  BuildKit's shell-word lexing (variables and quotes) and glob matching in
  `COPY` sources, which apply even to JSON-form instructions. For these unsafe names, copying to an
  existing directory without a trailing `/` lands as `file`; use a trailing
  `/` or a full target file path to preserve the intended name. A trailing-`/`
  remote appends the original, escaped basename, not `file`.
  A directory's contents sit directly under its positional path, so
  `COPY [".daytona-context/1", "/app"]` copies the contents into `/app` as
  `docker build` does. Positional paths make the rendered Dockerfile and archive
  the same on every machine, so an unchanged builder reuses the earlier upload.
- **A directory honours its own `.dockerignore`.** When the added directory
  has a `.dockerignore` at its root, its patterns exclude entries exactly as
  `docker build` excludes them from a context (the CLI's rules, below). The
  `.dockerignore` file itself is not copied.
- **`to_dockerfile()` renders the `COPY` lines** with the archive paths, so
  the Dockerfile it shows is the one that is built.
- **A `from_dockerfile` base ending in a line continuation** (its last
  instruction line ends with the escape character) cannot take further steps:
  BuildKit would join the next line to it, so an appended `COPY` or other step
  would silently become part of that instruction. Adding a step to such a base
  is a `BadRequestError`. The check follows BuildKit's parser: an escaped
  escape character (`\\`) ends no line, comment and whitespace-only lines
  inside a continuation are skipped, and an escape character alone on the
  last line carries nothing to join. A shared fixture generated from BuildKit
  holds both SDKs to it. A raw line (a `RUN` command, `WORKDIR`, `USER`) that
  ends in the base's escape character (for example a backtick under
  `# escape=\``) is refused where it is added, for the same reason. An
  unterminated heredoc in the base is not detected.
- **Builders stay immutable**: each call returns a new builder; the earlier
  one is unchanged.
- **Local files require the default Dockerfile escape character (`\`).** A
  `from_dockerfile` base that starts with parser directives selecting another
  escape character (for example, a backtick) is not supported with local files:
  appended-name escaping assumes `\`. Building such a builder raises
  `BadRequestError` before reading local files, with
  `local files need the default Dockerfile escape character`. Builders without
  local files are unaffected by this local-file restriction. `env()` also
  requires the default escape character, with its own error:
  `ImageBuilder.env cannot keep values literal under a Dockerfile escape directive other than a backslash`.
  Both checks use the same BuildKit parser-directive reader (including its
  BOM, whitespace and known-directive rules). Env values are literal: `$`,
  quotes and backslashes are escaped; C0 and DEL controls are refused without
  printing the value.
- **TypeScript needs Node.js for local files.** The local-file methods read
  the file system; in a runtime without it (a browser, a worker) they raise
  `NotSupportedError`. Builders without local files work everywhere.

### Building a builder with local files

`images.build(builder)` and `create_from_build(builder)` /
`createFromBuild(builder)` choose the request from the builder:

- **No local files:** unchanged, `POST /images` with `dockerfile` (or a
  registry `source` for a bare `from_registry`).
- **Local files:** the SDK writes a tar archive to a temporary file:
  `Dockerfile` at the root (the rendered Dockerfile), then each local source
  under its archive path. The archive goes through the build-context upload
  (the same code as `build_from_context`), and `POST /images` names the
  verified upload with `context_upload_id` and the upload's `region_id`. The
   temporary file is removed when the call returns or fails. The entries are
  collected before the temporary directory is created, so a temporary root
  inside the source tree is never archived. On Python
   cancellation the archive writer is signalled to stop and awaited before
   its temporary directory is removed. The 1 GiB
  context limit applies.

Archives are byte-identical across SDKs for the same tree and permission bits:
entries in UTF-8 byte order, modification times zero, owner `0:0` with empty
names, permission bits kept. Both write CPython's `tarfile.PAX_FORMAT` stream
layout: ASCII-replacement, truncated ustar fields, `././@PaxHeader` extensions
for names or link targets over 100 ASCII bytes or containing non-ASCII text,
and zero padding to a 10240-byte record boundary after the end markers.
ASCII controls such as tabs do not by themselves require a PAX extension.
A source path that is
a symbolic link when it is added is followed to its current target, as `docker
build` follows the context path it is given: a symlinked `requirements.txt` is
copied as its content. A source path that was not a symbolic link when it was
added, and at build time is one or resolves to another real path (a parent
directory swapped for a link), is refused with `local source changed since it
was added: "<path>"`, so a swap between declaration and build cannot redirect
the copy to another tree. Symbolic
links inside an added directory are archived as links; one whose target is
absolute or lexically leaves the directory with `..` is a `BadRequestError` naming the
path. Anything that is not a regular
file, a directory or a symbolic link (a socket, a device, a FIFO) is a
`BadRequestError` naming the path.

Regular files retain their device and inode identity from entry collection.
Opening uses no-follow and nonblocking flags where supported, and checks the
opened file against that identity before reading. A file replaced, or a
parent directory redirected to another tree, after its entry was collected is
refused with `BadRequestError`: `build context changed: "<path>"`, rather than
copying replacement contents. A directory replaced while the walk is listing
it is not detected: the walk reads directories by path, as `docker build`
does, so the source tree has to stay stable while it is archived.
Both SDKs copy exactly the size reported by `fstat` after opening, not the size
seen while walking. In-place growth before opening is included; growth after
opening is not read beyond that size. Size or modification-time changes alone
are not refused. A short read raises the same `build context changed` error.
This detects identity changes, not an atomic snapshot of concurrent in-place
writes; callers should keep the source tree stable during a build. A source
that changes between file and directory after declaration is refused at build
time as `local source is not a regular file: "<path>"` or
`local source is not a directory: "<path>"`. A source deleted between declaration
and build (`ENOENT` or `ENOTDIR` while resolving or stating the source), or one
replaced by a symbolic-link loop (`ELOOP`), raises `BadRequestError`:
`local source does not exist: "<path>"`. Python maps the `RuntimeError` that
`Path.resolve(strict=True)` raises for link loops on Python 3.10–3.12 to the
same error.

Opening a regular file with `EACCES` or `EPERM` raises `BadRequestError` with
`build context file is not readable: "<path>"`. `ELOOP`, `ENOENT`, `ENXIO` and
`ENOTDIR` raise `build context changed: "<path>"`; other I/O errors propagate.
Paths in these errors are JSON-quoted.
Reading a kept directory with `EACCES` or `EPERM` raises `BadRequestError`:
`build context directory is not readable: "<path>"`. Excluded unreadable
directories are skipped, even when an ignore-file negation would otherwise
require descending into them. Other directory I/O errors propagate.
Non-UTF-8 local source paths, names found during traversal (even excluded
names) and symbolic-link targets are refused with `BadRequestError`:
`build context path is not valid UTF-8: "<path>"`. The quoted path uses
replacement characters for undecodable bytes. Cancellation also stops Python's
entry walk, before archive writing starts.

### `build_from_context` takes a directory

`images.build_from_context(context)` / `images.buildFromContext(context)`
also accept the path of a directory (TypeScript: in Node.js). The directory
path follows a root symlink before traversal. Both directory and tar-file
paths use the same home-prefix rule as builder methods above.
The directory is archived the way `daytona image build --context <dir>` archives it, and the
way `docker build` reads a context: the ignore file is
`<dockerfile>.dockerignore` next to the Dockerfile when it exists, else
`.dockerignore` at the root; the Dockerfile and the ignore file are always
included; patterns are matched with BuildKit's rules. A tar file and bytes
are uploaded as given, as before.

For directories, `dockerfile` must be a relative path inside the context,
not an absolute path, a parent escape or the context root. It must resolve
to an existing regular file inside the resolved context root. A missing
Dockerfile or a symbolic-link loop fails locally with `BadRequestError`:
`dockerfile "<given>" does not exist in the build context`, preserving the
Dockerfile argument as given (including `./`). Python's older link-loop
`RuntimeError` maps to the same error. Selected ignore
files must also resolve to regular files within that root; their UTF-8 text
is decoded with replacement for malformed bytes in both SDKs.

The `.dockerignore` matcher is one implementation per language, ported from
the CLI's, and all three are tested against the same BuildKit cases
(`cli/internal/dockerignore/testdata/buildkit_cases.json`) and SDK edge cases
(`sdk-python/tests/fixtures/dockerignore-edge-cases.json`).
Matching is linear in path length for a fixed pattern, like Go's RE2, using token-state propagation rather than backtracking.
The regular-expression semantics are those of the Go version the CLI is built
with (Go 1.26), including Perl-mode bracket-class item and range parsing.
Unicode `\p` and `\P` classes use the runtime's Unicode tables (Python
`unicodedata`, Node ICU), not Go's tables. Newly assigned code points can
therefore match differently across Go and the SDK runtimes; for example,
`\pC` includes code points that the runtime considers unassigned. Single-letter
category names are case-insensitive (`\pc`), as in Go 1.25 and later.

### `debian_slim`

`debian_slim(python_version)` renders the 0.x base unchanged, so a ported
image builds the same layers:

```dockerfile
FROM python:<series>-slim-bookworm
RUN apt-get update
RUN apt-get install -y gcc gfortran build-essential
RUN pip install --upgrade pip
RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections
```

The series is one of `3.10` to `3.14`, default `3.13`; anything else,
including a micro version such as `3.12.10`, is a `BadRequestError`. 0.x
pinned a micro version per series; 2.x uses the series tag, so the base
receives Python patch releases. With no version, the 0.x Python SDK used the
series of the local interpreter (the 0.x TypeScript SDK used its newest
series); 2.x always defaults to `3.13`, so ported code should pass the version
it needs.

## Alternatives

- **Hash the local path into the archive path** (what 0.x did). The archive
  and the Dockerfile would differ between machines and checkouts, so an
  unchanged image would never reuse an upload. Rejected for positional names.
- **Ignore `.dockerignore` in `add_local_dir`**, as 0.x did. Adding a project
  directory would ship `.git`, virtual environments and `node_modules`.
  Rejected: the directory is treated as the context it usually is.
- **Port `pip_install_from_pyproject`.** It needs a TOML parser in both SDKs
  (TypeScript has none without a new dependency) and installed only the
  declared dependencies, never the project. `add_local_dir(project, "/app")`
  followed by `run_commands("python -m pip install /app")` installs the
  project with its dependencies. Not ported; the migration notes say so.
- **Port `dockerfile_commands` and `Image.from_dockerfile(path)`.** 2.x
  builders hold one instruction per line and `from_dockerfile` takes the
  Dockerfile's text. A Dockerfile on disk with its context is what
  `build_from_context(directory)` builds. Not ported.
- **Add `base(image)`.** It is `from_registry(image)` under another name.
  Not ported; the migration notes map it.
- **Teach the migration tool to rewrite `Image` chains.** The tool rewrites
  calls on client and sandbox receivers; `Image` chains are static calls with
  file-system arguments. The mapping notes list each 0.x method's 2.x
  counterpart instead.

## Consequences

- The parity exception recorded in [0001](0001-sdk-parity.md) for `user`,
  `cmd` and `entrypoint` is closed: both SDKs have them.
- Archive paths (`.daytona-context/<n>/…`) and the `debian_slim` layers are
  visible in `to_dockerfile()` output and in build logs. Changing them changes
  every rendered Dockerfile, so it is a deliberate change with a changelog
  entry, not a refactor.
- A builder with local files is built from a context; its first build
  uploads the archive, and later builds of the same files reuse it.
- The three `.dockerignore` implementations change together: a fix to one
  ports to the others and to the shared cases.
