/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { components } from "./api.js"

// Keep the API schema's `T | null` unions verbatim. Unlike the optional-field
// aliases in toolbox-types.ts, these responses preserve explicit null values.
type Schema<K extends keyof components["schemas"]> = components["schemas"][K]

export type AuditActionList = Schema<"AuditActionList">
export type AuditActionGroup = Schema<"AuditActionGroup">
export type BuildLogsUrl = Schema<"BuildLogsUrl">
export type BuildLogState = Schema<"BuildLogState">
export type BuildLogChunk = Schema<"BuildLogChunk">
export type BuildLogGap = Schema<"BuildLogGap">
export type BuildLogUnavailable = Schema<"BuildLogUnavailable">
export type BuildLogSourceChanged = Schema<"BuildLogSourceChanged">
export type BuildLogDone = Schema<"BuildLogDone">
export type BuildLogError = Schema<"BuildLogError">
export type SetSandboxPortRequest = Schema<"SetSandboxPortRequest">
export type DaytonaPreviewUrl = Schema<"PreviewUrl">
export type CreateImageRequest = Schema<"CreateImageRequest">
export type AsyncOperation = Schema<"AsyncOperation">
export type CreateImageResponse = AsyncOperation
export type CreateBuildContextUploadRequest = Schema<"CreateBuildContextUploadRequest">
export type CompleteBuildContextUploadRequest = Schema<"CompleteBuildContextUploadRequest">
export type BuildContextUpload = Schema<"BuildContextUpload">
export type BuildContextUploadPart = Schema<"BuildContextUploadPart">
export type CreateReservationRequest = Schema<"CreateReservationRequest">
export type CreateReservationsBulkRequest = Schema<"CreateReservationsBulkRequest">
export type Region = Schema<"Region">
export type RegionList = Schema<"RegionList">
export type MacReservationBatch = Omit<Schema<"MacReservationBatch">, 'reservations'> & {
  reservations: MacReservation[]
}
export type CreateSnapshotRequest = Schema<"CreateSandboxSnapshotRequest">
export type CreateSnapshotResponse = AsyncOperation
export type CreateSandboxResponse = Schema<"SandboxCreateOperation">
export type ProjectApiKeyWithContext = Schema<"ProjectApiKeyWithContext">
export type ProjectBudget = Schema<"ProjectBudget">
export type OrganizationLimits = Schema<"OrganizationLimits">
export type EndpointListResponse = Omit<Schema<"SandboxPortPage">, 'ports'> & {
  ports: PreviewEndpoint[]
}
export type ForkRequest = Schema<"ForkSandboxRequest">
export type ForkResponse = Schema<"ForkSandboxOperation">
export type ImageListItem = Schema<"ImageListItem">
export type ImagePage = Omit<Schema<"ImagePage">, 'images'> & {
  images: ImageListItem[]
}
export type ListReservationsResponse = Omit<Schema<"ReservationPage">, 'reservations'> & {
  reservations: MacReservation[]
}
export type ListSecretsResponse = Omit<Schema<"SecretPage">, 'secrets'> & {
  secrets: Secret[]
}
export type ListSnapshotsResponse = Omit<Schema<"SnapshotPage">, 'snapshots'> & {
  snapshots: Snapshot[]
}
export type ListSandboxesResponse = Omit<Schema<"SandboxPage">, 'sandboxes'> & {
  sandboxes: SandboxListItem[]
}
export type MacCatalog = Schema<"MacCatalog">
export type MacModel = Schema<"MacModel">
export type MacModelList = Omit<Schema<"MacModelList">, 'mac_models'> & {
  mac_models: MacModel[]
}
export type MacReservation = Schema<"MacReservation">
export type PreviewEndpoint = Schema<"SandboxPort">
export type PutSecretRequest = Schema<"SetSecretRequest">
export type Secret = Schema<"Secret">
export type SignFileUrlRequest = Schema<"SignSandboxFileUrlRequest">
export type SignedFileUrlResponse = Schema<"SignedFileUrl">
export type SignedPreviewUrlResponse = Schema<"SignedPreviewUrl">
export type Snapshot = Schema<"Snapshot">
export type UpdateReservationRequest = Schema<"UpdateReservationRequest">
export type Sandbox = Schema<"Sandbox">
export type SandboxListItem = Schema<"SandboxListItem">
export type CreateImageCaptureRequest = Schema<"CreateSandboxImageRequest">
export type CreateSandboxRequest = Schema<"CreateSandboxRequest">
export type CreateSandboxFromSnapshotRequest = Schema<"CreateSandboxFromSnapshotRequest">
export type CreateMacosSandboxRequest = Schema<"CreateMacosSandboxRequest">
export type PromoteImageRequest = Schema<"UpdateImageRequest">
export type ImageReference = Schema<"ImageReference">
export type SnapshotReference = Schema<"SnapshotReference">
export type CreateVolumeRequest = Schema<"CreateVolumeRequest">
export type ListVolumesResponse = Schema<"VolumePage">
export type Volume = Schema<"Volume">
