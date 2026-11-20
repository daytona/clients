// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

func parseReservations(rows []JSONValue) ([]MacReservation, error) {
	items := make([]MacReservation, 0, len(rows))
	for _, row := range rows {
		reservation, err := parseReservation(row)
		if err != nil {
			return nil, err
		}
		items = append(items, *reservation)
	}
	return items, nil
}

func parseReservation(raw JSONValue) (*MacReservation, error) {
	object, err := parseObject(raw, "reservation")
	if err != nil {
		return nil, err
	}
	reservation := &MacReservation{}
	if parseErr := parseReservationScalars(object, reservation); parseErr != nil {
		return nil, parseErr
	}
	reservation.Period, err = macPeriod(object)
	if err != nil {
		return nil, err
	}
	used, err := parseMacResources(object["resources_used"], "resources_used")
	if err != nil {
		return nil, err
	}
	reservation.ResourcesUsed = *used
	if present(object["capacity"]) {
		reservation.Capacity, err = parseMacResources(object["capacity"], "capacity")
		if err != nil {
			return nil, err
		}
	}
	reservation.Status = reservationStatus(reservation.RawStatus)
	reservation.Origin = reservationOrigin(reservation.RawOrigin)
	if present(object["end_reason"]) {
		reservation.EndReason = reservationEndReason(reservation.RawEndReason)
	}
	return reservation, nil
}

func parseReservationScalars(object jsonObject, reservation *MacReservation) error {
	var err error
	for _, entry := range []struct {
		name   string
		target *string
	}{
		{"id", &reservation.ID},
		{"org_id", &reservation.OrgID},
		{"project_id", &reservation.ProjectID},
		{"region_id", &reservation.Region},
		{"status", &reservation.RawStatus},
		{"origin", &reservation.RawOrigin},
	} {
		*entry.target, err = field[string](object, entry.name, true)
		if err != nil {
			return err
		}
	}
	for _, entry := range []struct {
		name   string
		target *string
	}{
		{"model", &reservation.Model},
		{"renewal_error", &reservation.RenewalError},
		{"end_reason", &reservation.RawEndReason},
	} {
		*entry.target, err = field[string](object, entry.name, false)
		if err != nil {
			return err
		}
	}
	for _, entry := range []struct {
		name   string
		target *int64
	}{
		{"slots_used", &reservation.SlotsUsed}, {"period_n", &reservation.PeriodN},
	} {
		*entry.target, err = integerField(object, entry.name, true)
		if err != nil {
			return err
		}
	}
	for _, entry := range []struct {
		name   string
		target *bool
	}{
		{"auto_renew", &reservation.AutoRenew}, {"renewal_pending", &reservation.RenewalPending},
	} {
		*entry.target, err = field[bool](object, entry.name, true)
		if err != nil {
			return err
		}
	}
	if !present(object["created_at"]) {
		return badRequest("response field created_at is required")
	}
	for _, entry := range []struct {
		name   string
		target *time.Time
	}{
		{"created_at", &reservation.CreatedAt},
		{"activated_at", &reservation.ActivatedAt},
		{"pending_mac_started_at", &reservation.PendingMacStartedAt},
		{"period_started_at", &reservation.PeriodStartedAt},
		{"period_end", &reservation.PeriodEnd},
		{"reissued_at", &reservation.ReissuedAt},
		{"ended_at", &reservation.EndedAt},
	} {
		*entry.target, err = timestampField(object, entry.name)
		if err != nil {
			return err
		}
	}
	return nil
}
