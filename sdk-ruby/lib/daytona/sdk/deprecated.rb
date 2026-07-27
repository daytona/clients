# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

# =============================================================================
# Backward-compatibility aliases. New functionality belongs in errors.rb, not
# here. Every name below exists solely so pre-typed-error-model user code
# keeps working, and this file can be removed as a whole in a future major
# release.
# =============================================================================
module Daytona
  module Sdk
    # @deprecated Use {BadRequestError} instead. Kept as an alias so existing
    #   `rescue Daytona::Sdk::ValidationError` blocks keep working.
    ValidationError = BadRequestError
  end
end
