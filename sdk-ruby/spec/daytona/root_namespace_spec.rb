# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::Daytona do
  let(:config) { build_config(target: nil) }
  let(:client) { described_class.new(config) }

  # A real client starts a background event-dispatcher connection; keep unit runs offline.
  before do
    allow(Daytona::EventSubscriptionManager).to receive(:new)
      .and_return(instance_double(Daytona::EventSubscriptionManager, shutdown: nil))
    allow(Daytona::EventDispatcher).to receive(:new)
      .and_return(instance_double(Daytona::EventDispatcher, ensure_connected: nil, disconnect: nil))
  end

  def capture_stderr
    original = $stderr
    $stderr = StringIO.new
    yield
    $stderr.string
  ensure
    $stderr = original
  end

  describe 'v1 namespace aliases' do
    it 'exposes the implementation class and aliased public constants under Daytona::V1' do
      expect(Daytona::V1::Daytona).to be(Daytona::V1::DaytonaV1)
      expect(Daytona::V1::Sandbox).to be(Daytona::Sandbox)
      expect(Daytona::V1::SandboxV1).to be(Daytona::Sandbox)
      expect(Daytona::V1::Image).to be(Daytona::Image)
      expect(Daytona::V1::ImageV1).to be(Daytona::Image)
      expect(Daytona::V1::Config).to be(Daytona::Config)
      expect(Daytona::V1::ConfigV1).to be(Daytona::Config)
      expect(Daytona::V1::ResourcesV1).to be(Daytona::Resources)
      expect(Daytona::V1::ListSandboxesQueryV1).to be(Daytona::ListSandboxesQuery)
      expect(Daytona::V1::Error).to be(Daytona::Error)
      expect(Daytona::V1::ValidationErrorV1).to be(Daytona::ValidationError)
      expect(Daytona::V1::AuthorizationErrorV1).to be(Daytona::AuthorizationError)
      expect(Daytona::V1::V1_API_URL).to eq(Daytona::V1_API_URL)
    end
  end

  describe '#initialize' do
    it 'creates a single v1 client instance and returns it on every access' do
      expect(client.v1).to be_a(Daytona::V1::Daytona)
      expect(client.v1).to be(client.v1)
      expect(client.v1.config).to eq(config)
    end
  end

  describe 'deprecated forwarding' do
    it 'warns once per process and forwards root calls to v1', :deprecation_warning do
      allow(client.v1).to receive(:create).and_return(:sandbox)
      allow(client.v1).to receive(:list).and_return([:sandbox])

      stderr = capture_stderr do
        expect(client.create).to eq(:sandbox)
        expect(client.list).to eq([:sandbox])
      end

      expect(stderr.scan('root form is removed in SDK 2.x').size).to eq(1)
      expect(stderr).to match(/Use `daytona\.v1\.create` instead/)
      expect(stderr).to start_with("#{__FILE__}:")

      expect(client.v1).to have_received(:create).once
      expect(client.v1).to have_received(:list).once
    end

    it 'forwards service readers to the exact same v1 objects' do
      expect(client.volume).to be(client.v1.volume)
      expect(client.secret).to be(client.v1.secret)
      expect(client.warm_pool).to be(client.v1.warm_pool)
      expect(client.snapshot).to be(client.v1.snapshot)
      expect(client.api_client).to be(client.v1.api_client)
      expect(client.sandbox_api).to be(client.v1.sandbox_api)
      expect(client.object_storage_api).to be(client.v1.object_storage_api)
      expect(client.snapshots_api).to be(client.v1.snapshots_api)
    end

    it 'does not warn when closing the root wrapper' do
      allow(client.v1).to receive(:close)

      expect { client.close }.not_to output.to_stderr
      expect(client.v1).to have_received(:close).once
    end
  end

  describe 'wrapper parity' do
    it 'defines every public v1 instance method on the root wrapper' do
      # The full public surface (including methods from included modules), minus Object's own.
      v1_methods = Daytona::V1::Daytona.public_instance_methods - Object.public_instance_methods
      root_methods = described_class.public_instance_methods - Object.public_instance_methods

      expect(v1_methods - root_methods).to eq([])
      expect(root_methods - v1_methods).to eq([:v1])
    end

    it 'gives every root forwarder the same parameters as its v1 method' do
      v1 = Daytona::V1::Daytona
      mismatched = (v1.public_instance_methods - Object.public_instance_methods).reject do |name|
        v1_method = v1.uninstrumented_method(name) || v1.instance_method(name)
        v1_method.parameters == described_class.instance_method(name).parameters
      end

      expect(mismatched).to eq([])
    end
  end

  describe 'forwarding of every deprecated method' do
    {
      create: [[:params], { on_snapshot_create_logs: :logs }],
      delete: [[:sandbox], { wait: true }],
      get: [[:id], {}],
      list: [[:query], {}],
      start: [%i[sandbox timeout], {}],
      stop: [%i[sandbox timeout], {}]
    }.each do |name, (args, kwargs)|
      it "forwards #{name} with its arguments" do
        allow(client.v1).to receive(name).and_return(:result)

        capture_stderr { expect(client.public_send(name, *args, **kwargs)).to eq(:result) }

        if kwargs.empty?
          expect(client.v1).to have_received(name).with(*args)
        else
          expect(client.v1).to have_received(name).with(*args, **kwargs)
        end
      end
    end
  end
end
