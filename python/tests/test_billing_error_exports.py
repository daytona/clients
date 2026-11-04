# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from daytona import v2


def test_billing_error_names_are_v2_exports() -> None:
    expected = {
        "AutomaticTopUpNotAvailableError",
        "BillingAccountConflictError",
        "BillingNotProvisionedError",
        "BillingUnavailableError",
        "ChargeNotFoundError",
        "ChargeNotSucceededError",
        "CouponAlreadyRedeemedError",
        "CouponExpiredError",
        "CouponLimitReachedError",
        "CouponNotFoundError",
        "InvoiceNotFinalizedError",
        "InvoiceNotFoundError",
        "InvoiceNotPayableError",
        "PaymentMethodRequiredError",
        "PlanChangePendingError",
        "PlanManagedByOperatorError",
        "PlanRequiresSalesError",
        "PlanUnchangedError",
        "UnknownPlanError",
    }

    assert expected <= set(v2.__all__)
