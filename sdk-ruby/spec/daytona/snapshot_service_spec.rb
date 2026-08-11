# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::SnapshotService do
  let(:snapshots_api) { instance_double(DaytonaApiClient::SnapshotsApi) }
  let(:object_storage_api) { instance_double(DaytonaApiClient::ObjectStorageApi) }
  let(:service) do
    described_class.new(
      snapshots_api: snapshots_api,
      object_storage_api: object_storage_api,
      default_region_id: 'us'
    )
  end

  describe '#list' do
    it 'returns PaginatedResource of Snapshots' do
      dto = build_snapshot_dto
      paginated = instance_double(DaytonaApiClient::PaginatedSnapshots, total: 1, page: 1, total_pages: 1, items: [dto])
      allow(snapshots_api).to receive(:get_all_snapshots).with(page: nil, limit: nil,
                                                               source_sandbox_id: nil).and_return(paginated)

      result = service.list

      expect(result).to be_a(Daytona::PaginatedResource)
      expect(result.items.first).to be_a(Daytona::Snapshot)
      expect(result.total).to eq(1)
    end

    it 'passes pagination params' do
      paginated = instance_double(DaytonaApiClient::PaginatedSnapshots, total: 5, page: 2, total_pages: 3, items: [])
      allow(snapshots_api).to receive(:get_all_snapshots).with(page: 2, limit: 10,
                                                               source_sandbox_id: nil).and_return(paginated)

      result = service.list(page: 2, limit: 10)

      expect(result.page).to eq(2)
    end

    it 'passes the source sandbox id filter' do
      paginated = instance_double(DaytonaApiClient::PaginatedSnapshots, total: 1, page: 1, total_pages: 1, items: [])
      allow(snapshots_api).to receive(:get_all_snapshots).with(page: nil, limit: nil,
                                                               source_sandbox_id: 'sandbox-1').and_return(paginated)

      result = service.list(source_sandbox_id: 'sandbox-1')

      expect(result.total).to eq(1)
    end

    it 'raises on invalid page' do
      expect { service.list(page: 0) }.to raise_error(Daytona::Sdk::Error, /page must be positive integer/)
    end

    it 'raises on invalid limit' do
      expect { service.list(limit: -1) }.to raise_error(Daytona::Sdk::Error, /limit must be positive integer/)
    end
  end

  describe '#get' do
    it 'returns Snapshot by name' do
      dto = build_snapshot_dto(name: 'my-snap')
      allow(snapshots_api).to receive(:get_snapshot).with('my-snap').and_return(dto)

      snapshot = service.get('my-snap')

      expect(snapshot).to be_a(Daytona::Snapshot)
      expect(snapshot.name).to eq('my-snap')
    end
  end

  describe '#delete' do
    let(:uuid) { '11111111-2222-4333-8444-555555555555' }

    it 'removes snapshot by id' do
      snapshot = Daytona::Snapshot.from_dto(build_snapshot_dto)
      allow(snapshots_api).to receive(:remove_snapshot).with('snap-123')

      service.delete(snapshot)

      expect(snapshots_api).to have_received(:remove_snapshot).with('snap-123')
    end

    it 'resolves a name to an id before deleting' do
      dto = build_snapshot_dto(id: 'snap-123', name: 'the-name')
      allow(snapshots_api).to receive(:get_snapshot).with('the-name').and_return(dto)
      allow(snapshots_api).to receive(:remove_snapshot).with('snap-123')

      service.delete('the-name')

      expect(snapshots_api).to have_received(:get_snapshot).with('the-name').ordered
      expect(snapshots_api).to have_received(:remove_snapshot).with('snap-123').ordered
    end

    it 'deletes by uuid string directly without resolving' do
      allow(snapshots_api).to receive(:remove_snapshot).with(uuid)
      allow(snapshots_api).to receive(:get_snapshot)

      service.delete(uuid)

      expect(snapshots_api).to have_received(:remove_snapshot).with(uuid)
      expect(snapshots_api).not_to have_received(:get_snapshot)
    end

    it 'falls back to name resolution when the optimistic uuid delete returns 404' do
      resolved_dto = build_snapshot_dto(id: 'snap-real', name: uuid)
      call_sequence = []
      allow(snapshots_api).to receive(:remove_snapshot) do |arg|
        call_sequence << [:remove_snapshot, arg]
        raise DaytonaApiClient::ApiError.new(code: 404, message: 'not found') if arg == uuid

        nil
      end
      allow(snapshots_api).to receive(:get_snapshot).with(uuid) do |arg|
        call_sequence << [:get_snapshot, arg]
        resolved_dto
      end

      service.delete(uuid)

      expect(call_sequence).to eq(
        [
          [:remove_snapshot, uuid],
          [:get_snapshot, uuid],
          [:remove_snapshot, 'snap-real']
        ]
      )
    end

    it 're-raises non-404 errors from the optimistic uuid delete without resolving' do
      error = DaytonaApiClient::ApiError.new(code: 403, message: 'forbidden')
      allow(snapshots_api).to receive(:remove_snapshot).with(uuid).and_raise(error)
      allow(snapshots_api).to receive(:get_snapshot)

      expect { service.delete(uuid) }.to raise_error(DaytonaApiClient::ApiError) do |e|
        expect(e.code).to eq(403)
      end
      expect(snapshots_api).not_to have_received(:get_snapshot)
    end
  end

  describe '#activate' do
    it 'activates a snapshot' do
      snapshot = Daytona::Snapshot.from_dto(build_snapshot_dto)
      activated_dto = build_snapshot_dto(state: 'active')
      allow(snapshots_api).to receive(:activate_snapshot).with('snap-123').and_return(activated_dto)

      result = service.activate(snapshot)

      expect(result).to be_a(Daytona::Snapshot)
      expect(result.state).to eq('active')
    end

    it 'resolves a name string before activating' do
      dto = build_snapshot_dto(id: 'snap-123', name: 'my-snap')
      activated_dto = build_snapshot_dto(id: 'snap-123', name: 'my-snap', state: 'active')
      allow(snapshots_api).to receive(:get_snapshot).with('my-snap').and_return(dto)
      allow(snapshots_api).to receive(:activate_snapshot).with('snap-123').and_return(activated_dto)

      result = service.activate('my-snap')

      expect(result).to be_a(Daytona::Snapshot)
      expect(result.state).to eq('active')
      expect(snapshots_api).to have_received(:get_snapshot).with('my-snap').ordered
      expect(snapshots_api).to have_received(:activate_snapshot).with('snap-123').ordered
    end
  end

  describe '#create' do
    it 'creates a snapshot from a string image and uses the default region' do
      created_dto = build_snapshot_dto(state: DaytonaApiClient::SnapshotState::ACTIVE)
      allow(snapshots_api).to receive(:create_snapshot).and_return(created_dto)

      params = Daytona::CreateSnapshotParams.new(name: 'my-snap', image: 'ubuntu:22.04', entrypoint: ['/bin/bash'])
      result = service.create(params)

      expect(result).to be_a(Daytona::Snapshot)
      expect(snapshots_api).to have_received(:create_snapshot) do |request|
        expect(request.name).to eq('my-snap')
        expect(request.image_name).to eq('ubuntu:22.04')
        expect(request.entrypoint).to eq(['/bin/bash'])
        expect(request.region_id).to eq('us')
      end
    end

    it 'sends region_ids without the default region so the API never receives both' do
      created_dto = build_snapshot_dto(state: DaytonaApiClient::SnapshotState::ACTIVE)
      allow(snapshots_api).to receive(:create_snapshot).and_return(created_dto)

      params = Daytona::CreateSnapshotParams.new(name: 'my-snap', image: 'ubuntu:22.04', region_ids: %w[us eu])
      service.create(params)

      expect(snapshots_api).to have_received(:create_snapshot) do |request|
        expect(request.region_ids).to eq(%w[us eu])
        expect(request.region_id).to be_nil
      end
    end

    it 'sends a single-element region_ids unchanged' do
      created_dto = build_snapshot_dto(state: DaytonaApiClient::SnapshotState::ACTIVE)
      allow(snapshots_api).to receive(:create_snapshot).and_return(created_dto)

      params = Daytona::CreateSnapshotParams.new(name: 'my-snap', image: 'ubuntu:22.04', region_ids: ['eu'])
      service.create(params)

      expect(snapshots_api).to have_received(:create_snapshot) do |request|
        expect(request.region_ids).to eq(['eu'])
        expect(request.region_id).to be_nil
      end
    end

    it 'rejects region_id and region_ids together without calling the API' do
      allow(snapshots_api).to receive(:create_snapshot)

      params = Daytona::CreateSnapshotParams.new(
        name: 'my-snap', image: 'ubuntu:22.04', region_id: 'us', region_ids: ['eu']
      )

      expect { service.create(params) }
        .to raise_error(Daytona::Sdk::Error, /Specify either region_id or region_ids/)
      expect(snapshots_api).not_to have_received(:create_snapshot)
    end

    it 'rejects an empty region_ids array without calling the API' do
      allow(snapshots_api).to receive(:create_snapshot)

      params = Daytona::CreateSnapshotParams.new(name: 'my-snap', image: 'ubuntu:22.04', region_ids: [])

      expect { service.create(params) }
        .to raise_error(Daytona::Sdk::Error, /region_ids must contain at least one region id/)
      expect(snapshots_api).not_to have_received(:create_snapshot)
    end

    it 'rejects an invalid region combination before uploading the image build context' do
      allow(described_class).to receive(:process_image_context).and_return(['ctx-hash'])
      allow(snapshots_api).to receive(:create_snapshot)

      params = Daytona::CreateSnapshotParams.new(
        name: 'my-snap', image: Daytona::Image.base('python:3.12'), region_id: 'us', region_ids: ['eu']
      )

      expect { service.create(params) }
        .to raise_error(Daytona::Sdk::Error, /Specify either region_id or region_ids/)
      expect(described_class).not_to have_received(:process_image_context)
      expect(snapshots_api).not_to have_received(:create_snapshot)
    end

    it 'creates a snapshot from an Image object with resources and image context' do
      image = Daytona::Image.base('python:3.12').entrypoint(['/bin/bash'])
      created_dto = build_snapshot_dto(state: DaytonaApiClient::SnapshotState::ACTIVE)
      allow(described_class).to receive(:process_image_context).and_return(['ctx-hash'])
      allow(snapshots_api).to receive(:create_snapshot).and_return(created_dto)

      params = Daytona::CreateSnapshotParams.new(
        name: 'image-snap',
        image: image,
        resources: Daytona::Resources.new(cpu: 2, memory: 4, disk: 8, gpu: 1),
        entrypoint: ['/usr/bin/env'],
        region_id: 'eu'
      )

      service.create(params)

      expect(described_class).to have_received(:process_image_context).with(object_storage_api, image)
      expect(snapshots_api).to have_received(:create_snapshot) do |request|
        expect(request.build_info.context_hashes).to eq(['ctx-hash'])
        expect(request.cpu).to eq(2)
        expect(request.memory).to eq(4)
        expect(request.disk).to eq(8)
        expect(request.gpu).to eq(1)
        expect(request.region_id).to eq('eu')
      end
    end

    it 'streams build logs and emits state transition messages' do
      pending_dto = build_snapshot_dto(id: 'snap-123', name: 'build-snap', state: DaytonaApiClient::SnapshotState::PENDING)
      building_dto = build_snapshot_dto(id: 'snap-123', name: 'build-snap', state: 'building')
      active_dto = build_snapshot_dto(id: 'snap-123', name: 'build-snap', state: DaytonaApiClient::SnapshotState::ACTIVE)
      logs_response = double('BuildLogsResponse', url: 'https://logs.example.com/snapshot')
      api_client = double('ApiClient')
      thread = double('Thread', join: true)
      on_logs = []

      allow(snapshots_api).to receive(:create_snapshot).and_return(pending_dto)
      allow(snapshots_api).to receive(:get_snapshot).with('snap-123').and_return(building_dto, active_dto)
      allow(snapshots_api).to receive(:get_snapshot_build_logs_url).with('snap-123').and_return(logs_response)
      allow(snapshots_api).to receive(:api_client).and_return(api_client)
      allow(api_client).to receive(:update_params_for_auth!)
      allow(Daytona::Util).to receive(:stream_async).and_return(thread)
      allow(service).to receive(:sleep)

      service.create(Daytona::CreateSnapshotParams.new(name: 'build-snap', image: 'ubuntu:22.04'), on_logs: lambda { |msg|
        on_logs << msg
      })

      expect(on_logs).to eq([
                              'Creating snapshot build-snap (pending)',
                              'Creating snapshot build-snap (building)',
                              'Created snapshot build-snap (active)'
                            ])
      expect(Daytona::Util).to have_received(:stream_async) do |uri:, headers:, on_chunk:|
        expect(uri.to_s).to eq('https://logs.example.com/snapshot?follow=true')
        expect(headers).to eq({})
        expect(on_chunk).to respond_to(:call)
      end
    end

    it 'raises on failed snapshot creation' do
      failed_dto = build_snapshot_dto(name: 'fail-snap', state: DaytonaApiClient::SnapshotState::BUILD_FAILED,
                                      error_reason: 'dockerfile error')
      allow(snapshots_api).to receive(:create_snapshot).and_return(failed_dto)

      params = Daytona::CreateSnapshotParams.new(name: 'fail-snap', image: 'bad:image')

      expect do
        service.create(params)
      end.to raise_error(Daytona::Sdk::Error,
                         /Failed to create snapshot fail-snap, reason: dockerfile error/)
    end
  end

  describe '.process_image_context' do
    it 'returns an empty array when no contexts exist' do
      image = Daytona::Image.base('python:3.12')

      expect(described_class.process_image_context(object_storage_api, image)).to eq([])
    end

    it 'uploads every image context using object storage push access credentials' do
      image = Daytona::Image.base('python:3.12')
      image.context_list << Daytona::Context.new(source_path: '/tmp/a', archive_path: 'a')
      image.context_list << Daytona::Context.new(source_path: '/tmp/b', archive_path: 'b')
      creds = double('PushAccess', storage_url: 'https://s3.example.com', access_key: 'key', secret: 'secret',
                                   session_token: 'token', bucket: 'bucket', organization_id: 'org-1',
                                   region: 'us-east-2')
      storage = instance_double(Daytona::ObjectStorage)

      allow(object_storage_api).to receive(:get_push_access).and_return(creds)
      allow(Daytona::ObjectStorage).to receive(:new).and_return(storage)
      allow(storage).to receive(:upload).and_return('hash-a', 'hash-b')

      result = described_class.process_image_context(object_storage_api, image)

      expect(result).to eq(%w[hash-a hash-b])
      expect(Daytona::ObjectStorage).to have_received(:new).with(hash_including(region: 'us-east-2'))
      expect(storage).to have_received(:upload).with('/tmp/a', 'org-1', 'a')
      expect(storage).to have_received(:upload).with('/tmp/b', 'org-1', 'b')
    end
  end
end
