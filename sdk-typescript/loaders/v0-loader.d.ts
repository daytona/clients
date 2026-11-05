/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The bundled v0 SDK's module exports, by name (a CommonJS module object). */
export type V0ModuleExports = { readonly [name: string]: unknown }

/**
 * The package-internal `#v0-loader` import (package.json "imports"): loads the
 * bundled v0 namespace synchronously (Node: a require of its CommonJS build;
 * browsers: a static import of its ESM build).
 */
export declare const loadV0: () => V0ModuleExports
