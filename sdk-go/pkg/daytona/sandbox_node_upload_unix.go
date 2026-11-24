// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build unix

package daytona

import "syscall"

// Open FIFOs without waiting for a writer so validation can reject them before uploading.
const nodeUploadNonblock = syscall.O_NONBLOCK
