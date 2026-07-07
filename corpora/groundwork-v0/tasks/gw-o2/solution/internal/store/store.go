// Package store is limitsvc's concurrency layer: an in-memory limiter whose
// Acquire takes a slot that Release returns.
package store

import "errors"

// ErrNoSlot is returned when the limiter is saturated.
var ErrNoSlot = errors.New("no slot available")

// Limiter is an in-memory counting semaphore over a fixed number of slots.
type Limiter struct{ free int }

// New returns a Limiter with a small pool of free slots.
func New() *Limiter { return &Limiter{free: 4} }

// Acquire takes a slot (the obligation's acquire anchor), or fails if the
// limiter is saturated.
func (l *Limiter) Acquire() error {
	if l.free <= 0 {
		return ErrNoSlot
	}
	l.free--
	return nil
}

// Release returns a slot to the limiter.
func (l *Limiter) Release() { l.free++ }
