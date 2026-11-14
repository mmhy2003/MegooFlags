package engine

import (
	"crypto/sha256"
	"encoding/binary"
	"math"
	"strconv"
)

// Buckets is the number of rollout buckets; weights add up to it.
const Buckets = 100000

// Weight is one variation's share of a rollout, out of Buckets.
type Weight struct {
	Variation string
	Weight    int
}

// BucketText is the text hashed for v, or false when v can't be bucketed
// (which counts as a missing attribute). A string is used as is, an integral
// number with |n| <= 2^53-1 as its base-10 text; anything else is refused.
func BucketText(v any) (string, bool) {
	if s, ok := v.(string); ok {
		return s, true
	}
	n, ok := toNumber(v)
	if !ok {
		return "", false
	}
	if n.isInt {
		if n.i > maxSafeInteger || n.i < -maxSafeInteger {
			return "", false
		}
		return strconv.FormatInt(n.i, 10), true
	}
	if n.f != math.Trunc(n.f) || math.Abs(n.f) > maxSafeInteger {
		return "", false // NaN and ±Inf fail one of these too
	}
	return strconv.FormatInt(int64(n.f), 10), true
}

// Bucket is uint64_be(sha256(flagKey + "." + text)[0:8]) mod Buckets.
func Bucket(flagKey, text string) int {
	sum := sha256.Sum256([]byte(flagKey + "." + text))
	return int(binary.BigEndian.Uint64(sum[:8]) % Buckets)
}

// pickVariation returns the first variation whose running total of weights
// is greater than bucket. Parse guarantees the weights add up to Buckets.
func pickVariation(ws []Weight, bucket int) string {
	total := 0
	for _, w := range ws {
		total += w.Weight
		if total > bucket {
			return w.Variation
		}
	}
	return ""
}
