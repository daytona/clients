# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::EventSubscriptionManager do
  describe '#subscribe' do
    it 'is a no-op when no dispatcher is configured' do
      manager = described_class.new(nil)

      sub_id = manager.subscribe(
        resource_id: 'sandbox-123',
        handler: proc { |_event_name, _data| },
        events: ['sandbox.state.updated']
      )

      expect(sub_id).to be_nil
      expect(manager.refresh('missing')).to be(false)
    end
  end
end
