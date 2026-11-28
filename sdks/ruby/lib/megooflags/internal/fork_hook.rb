# frozen_string_literal: true

module MegooFlags
  module Internal
    # ForkHook restarts the live providers in a forked child (Puma, Unicorn and Passenger fork
    # workers after the application has loaded). Only the forking thread survives a fork, so the
    # child would otherwise serve a ruleset that never updates and buffer events no thread sends.
    #
    # Process._fork is the one method every fork goes through (Kernel#fork, Process.fork and
    # IO.popen("-")), so a module prepended to its singleton class sees them all.
    module ForkHook
      # Prepended to Process.singleton_class.
      module Hook
        def _fork
          pid = super
          ForkHook.after_fork_in_child if pid.zero?
          pid
        end
      end

      # The started providers that are not shut down; weak, so a dropped provider is collected.
      @live = ObjectSpace::WeakMap.new
      @lock = Mutex.new
      @installed = false

      class << self
        # register remembers provider, which has a private restart_after_fork, and installs the hook
        # once per process.
        def register(provider)
          @lock.synchronize do
            install unless @installed
            @live[provider] = true
          end
          nil
        end

        def unregister(provider)
          @lock.synchronize { @live.delete(provider) }
          nil
        end

        # after_fork_in_child runs in the child, on the thread that forked. The lock may have been
        # held by a thread the child does not have, so it is replaced first.
        def after_fork_in_child
          @lock = Mutex.new
          # A snapshot: a restarted provider registers itself again.
          providers = @live.keys
          providers.each do |provider|
            provider.send(:restart_after_fork)
          rescue StandardError => e
            Kernel.warn("megooflags: could not restart the provider after fork: #{e.class}: #{e.message}")
          end
          nil
        end

        private

        def install
          Process.singleton_class.prepend(Hook) if Process.respond_to?(:_fork)
          @installed = true
        end
      end
    end
  end
end
