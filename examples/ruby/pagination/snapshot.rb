# frozen_string_literal: true

require 'daytona'

daytona = Daytona::Daytona.new.v1

result = daytona.snapshot.list(page: 2, limit: 10)
result.items.each do |snapshot|
  puts "#{snapshot.name} (#{snapshot.image_name})"
end
