// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleGit_Clone() {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	err = sandbox.Git().Clone(ctx, "https://github.com/octocat/Hello-World", "/home/daytona/repo",
		&daytona.GitCloneOptions{Depth: 1})
	if err != nil {
		log.Fatal(err)
	}
}

func ExampleGit_Commit() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	git := sandbox.Git()
	if addErr := git.Add(ctx, "/home/daytona/repo", []string{"README"}); addErr != nil {
		log.Fatal(addErr)
	}
	commit, err := git.Commit(ctx, "/home/daytona/repo", "Update README", &daytona.GitCommitOptions{
		Author: "SDK User", Email: "sdk@example.org",
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(commit.Hash)
}
