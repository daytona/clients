/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The v2 client reads its API URL from the env (DAYTONA_V2_API_URL, else
// DAYTONA_API_URL): point every test file at the offline host, and drop any
// per-namespace URL exported by the developer's shell, which would win.
delete process.env.DAYTONA_V2_API_URL
delete process.env.DAYTONA_V0_API_URL
process.env.DAYTONA_API_URL = 'http://api.invalid'
