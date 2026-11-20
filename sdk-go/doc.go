// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Package daytona provides a concurrency-safe client for Daytona Cloud.
//
// New reads DAYTONA_API_KEY, or DAYTONA_JWT_TOKEN with DAYTONA_PROJECT_ID.
// Explicit credential options take precedence over ambient credentials.
// DAYTONA_V2_API_URL is the only endpoint override.
//
// Get, List, Search and FindByName return live sandbox handles. Metadata is a
// caller-owned snapshot obtained with Sandbox.Info and reloaded with Refresh.
// ListAll and SearchAll are cancellable iterators. Close releases client
// resources and invalidates future calls through all its handles.
//
// Match failures using errors.Is and the category/code sentinels, or errors.As
// to inspect Error and its structured details. Caller context cancellation
// remains reachable through errors.Is.
package daytona
