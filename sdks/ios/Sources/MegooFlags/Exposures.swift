// Deduplicated exposures waiting to be sent to POST /sdk/v1/events. Recording
// runs on the read path, so it never does I/O and only holds a lock briefly.
import Foundation

/// An exposure records that a context saw a variant.
struct Exposure: Equatable, Sendable {
    let flagKey: String
    let variant: String
    let reason: String
    /// RFC 3339 UTC with milliseconds.
    let timestamp: String
    /// The request context, with sendContext only.
    let context: [String: JSONValue]?

    init(flagKey: String, variant: String, reason: String, timestamp: String, context: [String: JSONValue]? = nil) {
        self.flagKey = flagKey
        self.variant = variant
        self.reason = reason
        self.timestamp = timestamp
        self.context = context
    }

    /// json is the item of the events body; context only when set.
    var json: JSONValue {
        var o: [String: JSONValue] = [
            "flag_key": .string(flagKey), "variant": .string(variant), "reason": .string(reason),
            "timestamp": .string(timestamp),
        ]
        if let context {
            o["context"] = .object(context)
        }
        return .object(o)
    }
}

/// The buffered count that asks for an immediate flush, and the most items
/// one events request carries.
let exposureBatchSize = 1000

final class Exposures: @unchecked Sendable {
    private let sendContext: Bool
    private let logger: any MegooFlagsLogger
    private let now: @Sendable () -> Date
    private let maxKeys: Int
    private let maxBuffer: Int
    private let formatter = rfc3339Formatter()
    private let lock = NSLock()

    // The dedup LRU: seen maps each key to its latest touch; order lists
    // touches oldest first from head, and an entry whose touch is no longer
    // the key's latest is skipped. order is compacted in bulk.
    private var seen: [String: Int] = [:]
    private var order: [(key: String, touch: Int)] = []
    private var head = 0
    private var touches = 0

    private var buffer: [Exposure] = []
    private var warned = false
    private var fullSignalled = false
    private var full: (@Sendable () -> Void)?

    init(
        sendContext: Bool, logger: any MegooFlagsLogger, now: @escaping @Sendable () -> Date = { Date() },
        maxKeys: Int = 10_000, maxBuffer: Int = 10_000
    ) {
        self.sendContext = sendContext
        self.logger = logger
        self.now = now
        self.maxKeys = max(maxKeys, 1)
        self.maxBuffer = maxBuffer
    }

    /// onFull is called, outside the lock, when the buffer reaches 1000
    /// items; once until the next drain. The batcher sets it to flush.
    var onFull: (@Sendable () -> Void)? {
        get { locked { full } }
        set { locked { full = newValue } }
    }

    var count: Int { locked { buffer.count } }

    /// record buffers an exposure unless the same targeting key saw this
    /// flag and variant in the current UTC hour. A full buffer drops it,
    /// warning once until the next drain.
    func record(flag: String, variant: String, reason: String, body: [String: JSONValue]) {
        let date = now()
        let (warn, signal): (Bool, (@Sendable () -> Void)?) = locked {
            let timestamp = formatter.string(from: date)
            let key = "\(targetingKey(of: body) ?? "")|\(flag)|\(variant)|\(timestamp.prefix(13))"
            if seen[key] != nil {
                touch(key)
                return (false, nil)
            }
            if buffer.count >= maxBuffer {
                defer { warned = true }
                return (!warned, nil)
            }
            touch(key)
            while seen.count > maxKeys {
                let oldest = order[head]
                head += 1
                if seen[oldest.key] == oldest.touch {
                    seen[oldest.key] = nil
                }
            }
            buffer.append(
                Exposure(
                    flagKey: flag, variant: variant, reason: reason, timestamp: timestamp,
                    context: sendContext ? body : nil))
            guard buffer.count >= exposureBatchSize, !fullSignalled else { return (false, nil) }
            fullSignalled = true
            return (false, full)
        }
        if warn {
            warnFull()
        }
        signal?()
    }

    /// drain removes and returns up to max of the oldest exposures.
    func drain(max: Int) -> [Exposure] {
        locked {
            warned = false
            fullSignalled = false
            let n = min(max, buffer.count)
            let items = Array(buffer.prefix(n))
            buffer.removeFirst(n)
            return items
        }
    }

    /// restore puts items that could not be sent back in front of the
    /// buffer; the newest items over the bound are dropped.
    func restore(_ items: [Exposure]) {
        let warn: Bool = locked {
            buffer = items + buffer
            guard buffer.count > maxBuffer else { return false }
            buffer.removeLast(buffer.count - maxBuffer)
            defer { warned = true }
            return !warned
        }
        if warn {
            warnFull()
        }
    }

    /// touch makes key the most recently seen; call it holding the lock.
    private func touch(_ key: String) {
        touches += 1
        seen[key] = touches
        order.append((key, touches))
        if order.count > 2 * maxKeys + 64 {
            order = order[head...].filter { seen[$0.key] == $0.touch }
            head = 0
        }
    }

    private func warnFull() {
        logger.log(
            level: .warn, message: "megooflags: the exposure buffer is full (\(maxBuffer)); dropping new exposures")
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }
}
