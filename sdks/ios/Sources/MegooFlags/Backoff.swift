/// Backoff computes waits in seconds. The first wait is min(base, max); the
/// n-th is min(max, base * 2^n) scaled by a factor in [0.8, 1.0]. base can
/// be replaced, for the stream's retry field.
struct Backoff: Sendable {
    var base: Double
    let max: Double
    private let rand: @Sendable () -> Double
    private var n = 0

    init(base: Double, max: Double, rand: @escaping @Sendable () -> Double = { Double.random(in: 0..<1) }) {
        self.base = base
        self.max = max
        self.rand = rand
    }

    /// next returns the next wait and advances the attempt counter.
    mutating func next() -> Double {
        if n == 0 {
            n += 1
            return min(base, max)
        }
        let d = min(max, base * Double(sign: .plus, exponent: n, significand: 1))
        if n < 64 {
            n += 1
        }
        return d * (0.8 + 0.2 * rand())
    }

    /// reset makes the next wait base again.
    mutating func reset() {
        n = 0
    }
}
