// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleReservations_Create() {
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
	autoRenew := false
	// This purchases a billed reservation. Reuse the same key and options if
	// retrying after a lost response; do not generate a new key for that retry.
	reservation, err := client.Reservations().Create(context.Background(), "region-id", &daytona.CreateReservationOptions{
		IdempotencyKey: "my-purchase-request", AutoRenew: &autoRenew,
	})
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Println(reservation.ID, reservation.Status, reservation.Period)
}

func ExampleReservations_ListAll() {
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
	for reservation, err := range client.Reservations().ListAll(context.Background(), nil) {
		if err != nil {
			log.Print(err)
			return
		}
		fmt.Println(reservation.ID, reservation.RawStatus)
	}
}

func ExampleReservations_Update() {
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
	// Stop future automatic renewals; this does not refund the current period.
	reservation, err := client.Reservations().Update(context.Background(), "reservation-id", false)
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Println(reservation.AutoRenew, reservation.PeriodEnd)
}
