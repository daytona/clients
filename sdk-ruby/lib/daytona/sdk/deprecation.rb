# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

module Daytona
  module Sdk
    # One-time runtime warnings for the deprecated root client members and
    # Config#target. Has no dependencies, so any file can require it.
    module Deprecation
      ROOT_NAMESPACE_MESSAGE = 'Use `daytona.v1.%<member>s` instead. The root form is removed in SDK 2.x.'
      TARGET_MESSAGE =
        'Daytona::Config#target (and DAYTONA_TARGET) is deprecated and will be removed in a future version. ' \
        'Pass `target:` per sandbox create (`Daytona::CreateSandboxFromSnapshotParams` / ' \
        '`Daytona::CreateSandboxFromImageParams`) or `region_id:` per snapshot create ' \
        '(`Daytona::CreateSnapshotParams`) instead.'

      LOCK = Mutex.new
      private_constant :LOCK

      # Frames under this directory belong to the SDK; warnings point at the first frame outside it.
      SDK_DIR = "#{File.expand_path('..', __dir__)}#{File::SEPARATOR}".freeze
      private_constant :SDK_DIR

      module_function

      def warn_root_namespace_once(member)
        return unless first_warning?(:@root_namespace_warned)

        message = [
          "[DEPRECATION] Daytona::Daytona##{member} is deprecated.",
          format(ROOT_NAMESPACE_MESSAGE, member: member)
        ].join(' ')
        warn_from_caller(message)
      end

      def warn_target_once
        return unless first_warning?(:@target_warned)

        warn_from_caller("[DEPRECATION] #{TARGET_MESSAGE}")
      end

      # Emits a warning attributed to the first stack frame outside the SDK, so it points at user
      # code however deep the SDK call path is (e.g. through the root client wrapper).
      def warn_from_caller(message)
        locations = caller_locations(1) || []
        index = locations.index { |location| !location.absolute_path.to_s.start_with?(SDK_DIR) } || 0
        warn(message, uplevel: index + 1)
      end

      def reset_for_tests!
        LOCK.synchronize do
          @root_namespace_warned = false
          @target_warned = false
        end
      end

      # Atomically checks and sets the flag, so concurrent first uses warn once.
      def first_warning?(flag)
        LOCK.synchronize do
          next false if instance_variable_get(flag)

          instance_variable_set(flag, true)
        end
      end
    end
  end
end
