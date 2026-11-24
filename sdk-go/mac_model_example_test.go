// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleMacModels_List() {
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	models, err := client.MacModels().List(context.Background())
	if err != nil {
		log.Print(err)
		return
	}
	for _, model := range models {
		fmt.Println(model.ID, model.Cores, model.MemoryMiB)
	}
}

func ExampleMacModels_Catalog() {
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	catalog, err := client.MacModels().Catalog(context.Background(), nil)
	if err != nil {
		log.Print(err)
		return
	}
	for _, region := range catalog.Regions {
		for _, entry := range region.Models {
			fmt.Println(region.ID, entry.Model.ID, entry.Available)
			if entry.Price != nil {
				fmt.Println(entry.Price.PeriodCents, entry.Price.Currency, entry.Price.Period)
			}
		}
	}
}
