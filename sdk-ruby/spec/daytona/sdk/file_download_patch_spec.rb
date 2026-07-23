# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::Sdk::FileDownloadPatch do
  let(:api_error_class) do
    Class.new(StandardError) do
      attr_reader :code, :response_headers, :response_body

      def initialize(message = nil, code: nil, response_headers: nil, response_body: nil)
        super(message)
        @code = code
        @response_headers = response_headers
        @response_body = response_body
      end
    end
  end

  let(:api_client_class) do
    Class.new do
      def call_api(*_args)
        :ok
      end
    end
  end

  it 'does not re-alias call_api when apply! runs twice' do
    described_class.apply!(api_client_class, api_error_class)
    described_class.apply!(api_client_class, api_error_class)

    expect(api_client_class.new.call_api(:get, '/path')).to eq(:ok)
  end
end
