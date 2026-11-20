// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

// Build a reusable image with Python packages, start a sandbox from it, run a
// command, then delete both (the sandbox first).
func Example_imageToSandbox() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer closeClient(client)

	image, err := client.Images().BuildAndWait(ctx,
		daytona.NewImageBuilder("python:3.12-slim").PipInstall("pandas"),
		&daytona.BuildImageWaitOptions{BuildImageOptions: daytona.BuildImageOptions{Name: "py312-pandas"}})
	if err != nil {
		log.Print(err)
		return
	}
	sandbox, err := client.Create(ctx, image.ID, nil)
	if err != nil {
		log.Print(err)
		return
	}
	result, err := sandbox.Exec(ctx, `python3 -c "import pandas; print(pandas.__version__)"`, nil)
	if err == nil {
		fmt.Println(result.Stdout)
	}

	if err = sandbox.Delete(ctx, nil); err != nil {
		log.Print(err)
		return
	}
	// The platform releases the image a few seconds after the sandbox is
	// destroyed; until then the image delete is a conflict.
	for range 30 {
		err = client.Images().Delete(ctx, image.ID)
		if !errors.Is(err, daytona.ErrConflict) {
			break
		}
		time.Sleep(2 * time.Second)
	}
	if err != nil {
		log.Print(err)
	}
}

// Start a web server in a sandbox and get a public URL for it that opens
// without credentials.
func Example_publicURL() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer closeClient(client)

	sandbox, err := client.Create(ctx, "", nil)
	if err != nil {
		log.Print(err)
		return
	}
	defer deleteSandbox(ctx, sandbox)

	if _, err = sandbox.Process().Start(ctx, "python3 -m http.server 8080", nil); err != nil {
		log.Print(err)
		return
	}
	endpoint, err := sandbox.Ports().Expose(ctx, 8080, &daytona.ExposePortOptions{Public: true})
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Println(endpoint.URL) // open it in a browser

	// A script skips the preview warning page browsers may see first.
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.URL, nil)
	if err != nil {
		log.Print(err)
		return
	}
	request.Header.Set(daytona.PreviewWarningSkipHeader, daytona.PreviewWarningSkipHeaderValue)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := response.Body.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	fmt.Println(response.Status)
}

// Upload a local file, process it in the sandbox, and download the result.
func Example_fileRoundTrip() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer closeClient(client)

	sandbox, err := client.Create(ctx, "", nil)
	if err != nil {
		log.Print(err)
		return
	}
	defer deleteSandbox(ctx, sandbox)

	files := sandbox.FS()
	if err = files.UploadFile(ctx, "data.csv", "/home/daytona/data.csv", nil); err != nil {
		log.Print(err)
		return
	}
	if _, err = sandbox.Exec(ctx, "awk '{s+=$1} END {print s}' data.csv > result.txt",
		&daytona.ExecOptions{Cwd: "/home/daytona"}); err != nil {
		log.Print(err)
		return
	}
	if err = files.DownloadFile(ctx, "/home/daytona/result.txt", "result.txt", nil); err != nil {
		log.Print(err)
	}
}

func closeClient(client *daytona.Client) {
	if err := client.Close(); err != nil {
		log.Print(err)
	}
}

func deleteSandbox(ctx context.Context, sandbox *daytona.Sandbox) {
	if err := sandbox.Delete(ctx, nil); err != nil {
		log.Print(err)
	}
}
