//go:build !race

package engine

// raceEnabled is true under -race, where timing assertions are skipped.
const raceEnabled = false
