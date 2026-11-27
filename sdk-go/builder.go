// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"slices"
	"strings"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

// ImageBuilder is an immutable image recipe. Chaining methods return new
// builders, preserving earlier recipes and copying caller-owned inputs.
// Invalid input is retained and returned as ErrBadRequest by Dockerfile or Build.
// A zero-value builder has no base and is invalid.
type ImageBuilder struct {
	base    string
	raw     bool
	steps   []string
	sources []buildcontext.Source
	err     error
}

// NewImageBuilder starts with a registry reference, such as "python:3.13-slim".
// A bare registry builder is imported without a Dockerfile build. Invalid or
// multiline references produce ErrBadRequest when compiled or built.
func NewImageBuilder(base string) *ImageBuilder {
	ref, err := builderLine(base, "image base")
	// An image reference has no whitespace: "FROM python 3" is no image.
	if err == nil && strings.ContainsFunc(ref, builderSpace) {
		err = badRequest("image base must not contain whitespace")
	}
	return &ImageBuilder{base: ref, err: err}
}

// ImageFromDockerfile starts with Dockerfile text, rather than a filesystem path.
// Additional steps cannot follow an unfinished line continuation. Compilation
// reports invalid UTF-8 or blank text as ErrBadRequest.
func ImageFromDockerfile(text string) *ImageBuilder {
	err := builderText(text, "Dockerfile")
	return &ImageBuilder{base: builderTrim(text), raw: true, err: err}
}

// DebianSlim starts from python:<version>-slim-bookworm with compiler packages
// and an upgraded pip. Empty version defaults to "3.13"; supported series are
// "3.10" through "3.14". Unsupported versions fail compilation with ErrBadRequest.
func DebianSlim(version string) *ImageBuilder {
	selected := builderTrim(version)
	if version == "" {
		selected = "3.13"
	}
	switch selected {
	case "3.10", "3.11", "3.12", "3.13", "3.14":
		return NewImageBuilder("python:"+selected+"-slim-bookworm").RunCommands(
			"apt-get update",
			"apt-get install -y gcc gfortran build-essential",
			"pip install --upgrade pip",
			"echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections",
		)
	default:
		return &ImageBuilder{err: badRequest("Python version must be one of 3.10, 3.11, 3.12, 3.13, 3.14")}
	}
}

func (b *ImageBuilder) snapshot() ImageBuilder {
	if b == nil || b.base == "" && b.err == nil {
		return ImageBuilder{err: badRequest("an image builder requires a registry or Dockerfile base")}
	}
	return *b
}

func (b *ImageBuilder) appendSteps(steps []string, err error) *ImageBuilder {
	next := b.snapshot()
	if next.err == nil {
		next.err = err
	}
	if next.err == nil && len(steps) != 0 && next.raw && builderContinuation(next.base) {
		next.err = badRequest("Dockerfile ends with a line continuation; " +
			"an appended step would join its last instruction")
	}
	next.steps = append(slices.Clone(next.steps), steps...)
	return &next
}

// Dockerfile compiles the recipe to Dockerfile text with one trailing newline.
// It reports retained validation errors as ErrBadRequest without reading local
// file contents or making a network request.
func (b *ImageBuilder) Dockerfile() (string, error) {
	recipe := b.snapshot()
	if recipe.err != nil {
		return "", recipe.err
	}
	base := recipe.base
	if !recipe.raw {
		base = "FROM " + base
	}
	lines := append([]string{base}, recipe.steps...)
	return builderTrim(strings.Join(lines, "\n")) + "\n", nil
}

func (b *ImageBuilder) registrySource() (string, bool) {
	recipe := b.snapshot()
	return recipe.base, recipe.err == nil && !recipe.raw && len(recipe.steps) == 0
}

func (b *ImageBuilder) hasLocalSources() bool {
	return b != nil && len(b.sources) != 0
}
