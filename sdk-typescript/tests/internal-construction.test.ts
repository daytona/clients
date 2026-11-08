/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Sandbox, Image, Snapshot, ImagesService, SnapshotsService, VolumesService, SecretsService, ReservationsService, MacModelsService, PortsService, DockerService, SandboxVolumesService } from '../src/v2/index'

it.each([Sandbox, Image, Snapshot, ImagesService, SnapshotsService, VolumesService, SecretsService, ReservationsService, MacModelsService, PortsService, DockerService, SandboxVolumesService])('refuses external construction of %s', (constructor) => {
  // Given no access to the package-internal construction token.
  const token = Symbol('external')
  // When a JavaScript caller attempts construction.
  const construct = () => Reflect.construct(constructor, [token])
  // Then validation runs before any internal dependency is read.
  expect(construct).toThrow(TypeError)
})
