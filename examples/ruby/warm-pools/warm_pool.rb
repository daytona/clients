# frozen_string_literal: true

require 'daytona'

daytona = Daytona::Daytona.new

# Create a warm pool that keeps ready-to-use sandboxes for an existing snapshot.
# `target` is optional and defaults to the organization default region.
pool = daytona.warm_pool.create('my-snapshot', 3)
puts "Created warm pool #{pool.id} for snapshot '#{pool.snapshot}' in #{pool.target}"

# List warm pools. current_size vs pool is the status check: current_size is the
# number of ready sandboxes; error_reason is set when the pool cannot be filled.
daytona.warm_pool.list.each do |p|
  status = p.error_reason ? " (error: #{p.error_reason})" : ''
  puts "#{p.snapshot} (#{p.target}): #{p.current_size}/#{p.pool} ready#{status}"
end

# Grow the pool. Setting the size to 0 drains it without deleting the pool.
updated = daytona.warm_pool.update(pool.id, 5)
puts "Updated desired size to #{updated.pool}"

# Cleanup
daytona.warm_pool.delete(pool.id)
puts 'Deleted warm pool'
