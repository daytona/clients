# frozen_string_literal: true

RSpec.describe Daytona::WarmPoolService do
  let(:warm_pools_api) { instance_double(DaytonaApiClient::WarmPoolsApi) }
  let(:service) { described_class.new(warm_pools_api) }

  describe '#create' do
    it 'creates a warm pool and returns a WarmPool model' do
      dto = build_warm_pool_dto(snapshot: 'my-snapshot', pool: 5)
      allow(warm_pools_api).to receive(:create_warm_pool).and_return(dto)

      warm_pool = service.create('my-snapshot', 5)

      expect(warm_pool).to be_a(Daytona::WarmPool)
      expect(warm_pool.snapshot).to eq('my-snapshot')
      expect(warm_pool.pool).to eq(5)
      expect(warm_pools_api).to have_received(:create_warm_pool) do |request|
        expect(request.snapshot).to eq('my-snapshot')
        expect(request.pool).to eq(5)
        expect(request.target).to be_nil
      end
    end

    it 'passes the target region when given' do
      dto = build_warm_pool_dto(target: 'eu')
      allow(warm_pools_api).to receive(:create_warm_pool).and_return(dto)

      service.create('my-snapshot', 5, target: 'eu')

      expect(warm_pools_api).to have_received(:create_warm_pool) do |request|
        expect(request.target).to eq('eu')
      end
    end
  end

  describe '#delete' do
    it 'deletes a warm pool by id' do
      allow(warm_pools_api).to receive(:delete_warm_pool).with('wp-123')

      service.delete('wp-123')

      expect(warm_pools_api).to have_received(:delete_warm_pool).with('wp-123')
    end
  end

  describe '#list' do
    it 'lists warm pools as WarmPool models' do
      allow(warm_pools_api).to receive(:list_warm_pools).and_return([build_warm_pool_dto])

      warm_pools = service.list

      expect(warm_pools.length).to eq(1)
      expect(warm_pools.first).to be_a(Daytona::WarmPool)
      expect(warm_pools.first.current_size).to eq(3)
    end
  end

  describe '#update' do
    it 'updates the desired pool size' do
      dto = build_warm_pool_dto(pool: 10)
      allow(warm_pools_api).to receive(:update_warm_pool).and_return(dto)

      warm_pool = service.update('wp-123', 10)

      expect(warm_pool).to be_a(Daytona::WarmPool)
      expect(warm_pool.pool).to eq(10)
      expect(warm_pools_api).to have_received(:update_warm_pool) do |id, request|
        expect(id).to eq('wp-123')
        expect(request.pool).to eq(10)
      end
    end
  end
end
