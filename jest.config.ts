/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { getJestProjectsAsync } from '@nx/jest'

export default async () => ({
  projects: await getJestProjectsAsync(),
})
