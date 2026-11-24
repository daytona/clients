// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleReservations_CreateBulk() {
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
	// Purchase up to three Macs, accepting no fewer than two.
	batch, err := client.Reservations().CreateBulk(
		context.Background(), "region-id", 3, &daytona.CreateReservationsBulkOptions{
			MinCount: 2, IdempotencyKey: "my-bulk-purchase",
		},
	)
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Println(batch.Requested, batch.Reserved)
}

func ExampleReservations_Renew() {
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
	// This pays for another period. Keep the key stable if retrying this renewal.
	reservation, err := client.Reservations().Renew(
		context.Background(), "reservation-id", &daytona.RenewReservationOptions{
			IdempotencyKey: "my-next-period",
		},
	)
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Println(reservation.PeriodN, reservation.PeriodEnd)
}
