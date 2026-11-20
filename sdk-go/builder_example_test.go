// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleImageBuilder() {
	builder := daytona.NewImageBuilder("python:3.13-slim").
		PipInstall("requests>=2").
		Env("APP_MODE", "production").
		Workdir("/app").
		Cmd("python", "app.py")
	dockerfile, err := builder.Dockerfile()
	if err != nil {
		log.Fatal(err)
	}
	fmt.Print(dockerfile)
	// Output:
	// FROM python:3.13-slim
	// RUN python -m pip install 'requests>=2'
	// ENV APP_MODE="production"
	// WORKDIR /app
	// CMD ["python", "app.py"]
}

func ExampleImageBuilder_AddLocalDir() {
	builder := daytona.DebianSlim("").
		AddLocalDir("./src", "/app").
		PipInstallFromRequirements("./requirements.txt").
		Workdir("/app").
		Cmd("python", "main.py")
	dockerfile, err := builder.Dockerfile()
	if err != nil {
		log.Fatal(err)
	}
	fmt.Print(dockerfile)
}
