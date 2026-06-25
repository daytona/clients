# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::Secret do
  describe '#initialize' do
    it 'populates attributes from DTO' do
      dto = build_secret_dto(
        id: 'secret-abc',
        name: 'anthropic-prod',
        description: 'Production API key',
        placeholder: 'daytona-secret-abc',
        hosts: ['api.anthropic.com', '*.example.com'],
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-02T00:00:00Z'
      )

      secret = described_class.new(dto)

      expect(secret.id).to eq('secret-abc')
      expect(secret.name).to eq('anthropic-prod')
      expect(secret.description).to eq('Production API key')
      expect(secret.placeholder).to eq('daytona-secret-abc')
      expect(secret.hosts).to eq(['api.anthropic.com', '*.example.com'])
      expect(secret.created_at).to eq('2025-01-01T00:00:00Z')
      expect(secret.updated_at).to eq('2025-01-02T00:00:00Z')
    end

    it 'allows a nil description' do
      dto = build_secret_dto(description: nil)
      secret = described_class.new(dto)
      expect(secret.description).to be_nil
    end

    it 'does not expose the plaintext value' do
      expect(described_class.instance_methods).not_to include(:value)
    end
  end
end
