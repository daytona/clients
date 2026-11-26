/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Bundled by the tarball smoke with minification (esbuild --minify, webpack
// production): the SDK's error classes keep their names, instanceof across
// the root and v2 still holds, and an application subclass keeps its own name.
// Minification renames the application's class too, so it names itself the
// way the SDK's classes do; the check proves its errors take that name, not
// the SDK base class's.
import { DaytonaNotFoundError } from '@daytona/sdk'
import { ConnectionError, NotFoundError } from '@daytona/sdk/v2'

class AppNotFoundError extends NotFoundError {}
Object.defineProperty(AppNotFoundError, 'name', { value: 'AppNotFoundError' })

const notFound = new NotFoundError('missing')
const mine = new AppNotFoundError('mine')
console.log([
  notFound.name,
  String(new ConnectionError('Failed to fetch')),
  new DaytonaNotFoundError('root').name,
  notFound instanceof DaytonaNotFoundError,
  mine instanceof NotFoundError && mine.name === 'AppNotFoundError',
].join(' '))
