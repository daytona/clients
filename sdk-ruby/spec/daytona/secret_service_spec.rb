# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::SecretService do
  let(:secret_api) { instance_double(DaytonaApiClient::SecretApi) }
  let(:service) { described_class.new(secret_api) }

  describe '#create' do
    it 'creates a secret and returns a Secret model' do
      dto = build_secret_dto(name: 'anthropic-prod')
      allow(secret_api).to receive(:create_secret).and_return(dto)

      secret = service.create('anthropic-prod', 'sk-secret-value',
                              description: 'Prod key', hosts: ['api.anthropic.com'])

      expect(secret).to be_a(Daytona::Secret)
      expect(secret.name).to eq('anthropic-prod')
      expect(secret_api).to have_received(:create_secret) do |request|
        expect(request.name).to eq('anthropic-prod')
        expect(request.value).to eq('sk-secret-value')
        expect(request.description).to eq('Prod key')
        expect(request.hosts).to eq(['api.anthropic.com'])
      end
    end

    it 'creates a secret without optional params' do
      dto = build_secret_dto(name: 'minimal')
      allow(secret_api).to receive(:create_secret).and_return(dto)

      service.create('minimal', 'value-only')

      expect(secret_api).to have_received(:create_secret) do |request|
        expect(request.name).to eq('minimal')
        expect(request.value).to eq('value-only')
        expect(request.description).to be_nil
        expect(request.hosts).to be_nil
      end
    end
  end

  describe '#delete' do
    it 'deletes a secret by id' do
      allow(secret_api).to receive(:delete_secret).with('secret-123')

      service.delete('secret-123')

      expect(secret_api).to have_received(:delete_secret).with('secret-123')
    end
  end

  describe '#get' do
    it 'gets a secret by id' do
      dto = build_secret_dto(id: 'secret-9', name: 'my-secret')
      allow(secret_api).to receive(:get_secret).with('secret-9').and_return(dto)

      secret = service.get('secret-9')

      expect(secret).to be_a(Daytona::Secret)
      expect(secret.id).to eq('secret-9')
      expect(secret.name).to eq('my-secret')
    end

    it 'propagates not found errors' do
      error = DaytonaApiClient::ApiError.new(code: 404, message: 'Secret not found')
      allow(secret_api).to receive(:get_secret).and_raise(error)

      expect { service.get('missing') }.to raise_error(DaytonaApiClient::ApiError)
    end
  end

  describe '#list' do
    it 'returns an array of Secret models' do
      dtos = [build_secret_dto(name: 's1'), build_secret_dto(name: 's2')]
      allow(secret_api).to receive(:list_secrets).and_return(dtos)

      secrets = service.list

      expect(secrets).to all(be_a(Daytona::Secret))
      expect(secrets.map(&:name)).to eq(%w[s1 s2])
    end
  end

  describe '#update' do
    it 'updates a secret and returns a Secret model' do
      dto = build_secret_dto(id: 'secret-9', name: 'my-secret')
      allow(secret_api).to receive(:update_secret).and_return(dto)

      secret = service.update('secret-9', value: 'rotated', description: 'New desc',
                                          hosts: ['*.example.com'])

      expect(secret).to be_a(Daytona::Secret)
      expect(secret_api).to have_received(:update_secret) do |secret_id, request|
        expect(secret_id).to eq('secret-9')
        expect(request.value).to eq('rotated')
        expect(request.description).to eq('New desc')
        expect(request.hosts).to eq(['*.example.com'])
      end
    end

    it 'updates with no optional params' do
      dto = build_secret_dto(id: 'secret-9')
      allow(secret_api).to receive(:update_secret).and_return(dto)

      service.update('secret-9')

      expect(secret_api).to have_received(:update_secret) do |secret_id, request|
        expect(secret_id).to eq('secret-9')
        expect(request.value).to be_nil
        expect(request.description).to be_nil
        expect(request.hosts).to be_nil
      end
    end
  end
end
