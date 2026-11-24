// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleClient_ListAuditActions() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	actions, err := client.ListAuditActions(context.Background())
	if err != nil {
		log.Fatal(err)
	}
	for _, group := range actions.Targets {
		fmt.Println(group.TargetType, group.Actions)
	}
}

func ExampleClient_CurrentAPIKey() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	key, err := client.CurrentAPIKey(context.Background())
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(key.Name, key.Context.ProjectName)
}

func ExampleClient_ProjectBudget() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	budget, err := client.ProjectBudget(context.Background())
	if err != nil {
		log.Fatal(err)
	}
	if budget.MonthlyLimitCents != nil {
		fmt.Println("Monthly budget in cents:", *budget.MonthlyLimitCents)
	}
}

func ExampleClient_Limits() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	limits, err := client.Limits(context.Background(), nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("Running sandboxes:", limits.Sandboxes.Concurrent.Usage)
	if limits.Sandboxes.Concurrent.Limit == nil {
		fmt.Println("No concurrency limit")
	}
}
