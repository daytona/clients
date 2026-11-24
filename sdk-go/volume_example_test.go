// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleVolumes_Create() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()

	ctx := context.Background()
	volume, err := client.Volumes().Create(ctx, "shared-data", &daytona.CreateVolumeOptions{SizeGiB: 8})
	if err != nil {
		log.Fatal(err)
	}
	log.Print(volume.ID, volume.Status)
	if deleteErr := client.Volumes().Delete(ctx, volume.ID, &daytona.DeleteVolumeOptions{IDOnly: true}); deleteErr != nil {
		log.Print(deleteErr)
	}
}

func ExampleVolumes_ListAll() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	for volume, err := range client.Volumes().ListAll(context.Background(), nil) {
		if err != nil {
			log.Print(err)
			break
		}
		log.Print(volume.Name)
	}
}
