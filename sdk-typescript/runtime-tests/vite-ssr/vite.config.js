/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Vite leaves node_modules packages external in SSR builds by default; bundle
// the SDK into the server bundle (its dependencies stay external).
export default {
  ssr: { noExternal: ['@daytona/sdk'] },
}
