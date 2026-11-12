/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The v2 client reads its API URL from DAYTONA_V2_API_URL: point every test
// file at the offline host, and drop any v0 URL exported by the developer's
// shell. DAYTONA_API_URL is set to a host no test may reach: 2.x never reads it.
process.env.DAYTONA_V2_API_URL = 'http://api.invalid'
delete process.env.DAYTONA_V0_API_URL
process.env.DAYTONA_API_URL = 'http://never-read-by-2x.invalid'
