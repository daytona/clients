// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "strings"

// RunCommands adds one RUN instruction per command. Commands must be nonblank
// single lines; invalid commands fail compilation with ErrBadRequest.
func (b *ImageBuilder) RunCommands(commands ...string) *ImageBuilder {
	if len(commands) == 0 {
		return b.appendSteps(nil, badRequest("RunCommands requires at least one command"))
	}
	lines := make([]string, len(commands))
	for i, command := range commands {
		text, err := b.rawLine(command, "RUN command")
		if err != nil {
			return b.appendSteps(nil, err)
		}
		lines[i] = "RUN " + text
	}
	return b.appendSteps(lines, nil)
}

// PipInstall adds python -m pip install with POSIX-shell-quoted packages.
// At least one nonblank package is required; pip options can be written with
// RunCommands. Invalid input fails compilation with ErrBadRequest.
func (b *ImageBuilder) PipInstall(packages ...string) *ImageBuilder {
	if len(packages) == 0 {
		return b.appendSteps(nil, badRequest("PipInstall requires at least one package"))
	}
	quoted := make([]string, len(packages))
	for i, pkg := range packages {
		text, err := builderLine(pkg, "pip package")
		if err != nil {
			return b.appendSteps(nil, err)
		}
		quoted[i] = builderShellQuote(text)
	}
	return b.RunCommands("python -m pip install " + strings.Join(quoted, " "))
}

// Workdir adds WORKDIR. Dockerfile variable substitution is preserved. Blank,
// multiline, or continuation-ending paths fail compilation with ErrBadRequest.
func (b *ImageBuilder) Workdir(path string) *ImageBuilder {
	text, err := b.rawLine(path, "WORKDIR path")
	return b.appendSteps([]string{"WORKDIR " + text}, err)
}

// User adds USER. Blank, multiline, or continuation-ending names fail
// compilation with ErrBadRequest.
func (b *ImageBuilder) User(name string) *ImageBuilder {
	text, err := b.rawLine(name, "USER name")
	return b.appendSteps([]string{"USER " + text}, err)
}

// Cmd adds an exec-form CMD, preserving argument whitespace and Unicode.
// At least one argument is required, and each must be nonblank valid UTF-8.
// Invalid input fails compilation with ErrBadRequest.
func (b *ImageBuilder) Cmd(args ...string) *ImageBuilder {
	return b.jsonCommand("CMD", args)
}

// Entrypoint adds an exec-form ENTRYPOINT with the same validation as Cmd.
func (b *ImageBuilder) Entrypoint(args ...string) *ImageBuilder {
	return b.jsonCommand("ENTRYPOINT", args)
}

func (b *ImageBuilder) jsonCommand(instruction string, args []string) *ImageBuilder {
	if len(args) == 0 {
		return b.appendSteps(nil, badRequest(instruction+" requires at least one argument"))
	}
	for _, arg := range args {
		if err := builderText(arg, instruction+" argument"); err != nil {
			return b.appendSteps(nil, err)
		}
	}
	return b.appendSteps([]string{instruction + " " + builderArray(args)}, nil)
}
