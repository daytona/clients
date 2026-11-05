# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest
from pydantic import ValidationError

from daytona.v2._gen.api.models.payment_method_brand import PaymentMethodBrand
from daytona.v2._gen.api.models.redeem_coupon_request import RedeemCouponRequest


def test_coupon_code_rejects_trailing_newline() -> None:
    with pytest.raises(ValidationError):
        RedeemCouponRequest(code="SAVE10\n")


def test_unknown_payment_method_brand_reuses_pseudo_member() -> None:
    first = PaymentMethodBrand("future_brand")

    second = PaymentMethodBrand("future_brand")

    assert first is second
