# frozen_string_literal: true

require 'daytona'

daytona = Daytona::Daytona.new.v1

daytona.list(Daytona::V1::ListSandboxesQuery.new(
               limit: 10,
               labels: { 'env' => 'dev' },
               states: [Daytona::V1::SandboxState::STARTED],
               sort: Daytona::V1::SandboxListSortField::CREATED_AT,
               order: Daytona::V1::SandboxListSortDirection::DESC
             )).each do |sandbox|
  puts sandbox.id
end
