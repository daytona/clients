// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

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
