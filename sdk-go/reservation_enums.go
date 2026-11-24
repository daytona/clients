// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// MacReservationStatus is an observed reservation state.
type MacReservationStatus string

const (
	// MacReservationStatusUnknown indicates an unrecognized RawStatus.
	MacReservationStatusUnknown MacReservationStatus = "unknown"
	// MacReservationStatusPendingCharge means payment is pending.
	MacReservationStatusPendingCharge MacReservationStatus = "pending_charge"
	// MacReservationStatusPendingMac means a Mac is being assigned.
	MacReservationStatusPendingMac MacReservationStatus = "pending_mac"
	// MacReservationStatusActive means the reservation is active.
	MacReservationStatusActive MacReservationStatus = "active"
	// MacReservationStatusEnding means the reservation is ending.
	MacReservationStatusEnding MacReservationStatus = "ending"
	// MacReservationStatusReleased means the Mac has been released.
	MacReservationStatusReleased MacReservationStatus = "released"
	// MacReservationStatusFailed means the reservation failed.
	MacReservationStatusFailed MacReservationStatus = "failed"
)

// MacReservationOrigin describes how a reservation was purchased.
type MacReservationOrigin string

const (
	// MacReservationOriginUnknown indicates an unrecognized RawOrigin.
	MacReservationOriginUnknown MacReservationOrigin = "unknown"
	// MacReservationOriginExplicit means the reservation was explicitly purchased.
	MacReservationOriginExplicit MacReservationOrigin = "explicit"
	// MacReservationOriginAuto means auto-reserve purchased the reservation.
	MacReservationOriginAuto MacReservationOrigin = "auto"
)

// MacReservationEndReason describes why a reservation ended; empty means absent.
type MacReservationEndReason string

const (
	// MacReservationEndReasonUnknown indicates an unrecognized RawEndReason.
	MacReservationEndReasonUnknown MacReservationEndReason = "unknown"
	// MacReservationEndReasonExpired means the period expired.
	MacReservationEndReasonExpired MacReservationEndReason = "expired"
	// MacReservationEndReasonReleased means the reservation was released.
	MacReservationEndReasonReleased MacReservationEndReason = "released"
	// MacReservationEndReasonChargeFailed means payment failed.
	MacReservationEndReasonChargeFailed MacReservationEndReason = "charge_failed"
	// MacReservationEndReasonNotAccepted means the reservation was not accepted.
	MacReservationEndReasonNotAccepted MacReservationEndReason = "not_accepted"
	// MacReservationEndReasonReissued means the reservation was reissued.
	MacReservationEndReasonReissued MacReservationEndReason = "reissued"
	// MacReservationEndReasonOperator means an operator ended the reservation.
	MacReservationEndReasonOperator MacReservationEndReason = "operator"
)

func reservationStatus(raw string) MacReservationStatus {
	switch MacReservationStatus(raw) {
	case MacReservationStatusUnknown, MacReservationStatusPendingCharge, MacReservationStatusPendingMac,
		MacReservationStatusActive,
		MacReservationStatusEnding, MacReservationStatusReleased, MacReservationStatusFailed:
		return MacReservationStatus(raw)
	default:
		return MacReservationStatusUnknown
	}
}

func reservationOrigin(raw string) MacReservationOrigin {
	switch MacReservationOrigin(raw) {
	case MacReservationOriginUnknown, MacReservationOriginExplicit, MacReservationOriginAuto:
		return MacReservationOrigin(raw)
	default:
		return MacReservationOriginUnknown
	}
}

func reservationEndReason(raw string) MacReservationEndReason {
	switch MacReservationEndReason(raw) {
	case MacReservationEndReasonUnknown, MacReservationEndReasonExpired, MacReservationEndReasonReleased,
		MacReservationEndReasonChargeFailed,
		MacReservationEndReasonNotAccepted, MacReservationEndReasonReissued, MacReservationEndReasonOperator:
		return MacReservationEndReason(raw)
	default:
		return MacReservationEndReasonUnknown
	}
}
