// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package util

import (
	"github.com/charmbracelet/lipgloss"
	"go.daytona.io/cli/views/common"
)

const PropertyNameWidth = 16

var PropertyNameStyle = lipgloss.NewStyle().
	Foreground(common.LightGray)

var PropertyValueStyle = lipgloss.NewStyle().
	Foreground(common.Light).
	Bold(true)
