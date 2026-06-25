#!/bin/bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0


# Clean up existing documentation files
rm -rf docs hack/docs

# Generate default CLI documentation files in folder "docs"
go run main.go generate-docs
