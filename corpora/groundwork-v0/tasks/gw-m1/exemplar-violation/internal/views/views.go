// Package views holds the counter seam. Counter has three implementations; main
// wires exactly one live. A caller cannot tell from the interface alone whether
// a Bump touches the database — only the wired implementation decides that.
package views

import (
	"context"

	"example.com/accountsvc/internal/repo"
)

// Counter records an occurrence against an id and returns the running total.
type Counter interface {
	Bump(ctx context.Context, id string) (int, error)
}

// MemCounter keeps counts in process memory (no external effect).
type MemCounter struct{ n map[string]int }

// NewMem returns an empty MemCounter.
func NewMem() *MemCounter { return &MemCounter{n: map[string]int{}} }

// Bump increments the in-memory count for id.
func (c *MemCounter) Bump(_ context.Context, id string) (int, error) {
	c.n[id]++
	return c.n[id], nil
}

// NopCounter counts nothing (used where a counter is optional).
type NopCounter struct{}

// Bump does nothing.
func (NopCounter) Bump(_ context.Context, _ string) (int, error) { return 0, nil }

// LedgerCounter persists counts through the repository (a DB write per Bump).
type LedgerCounter struct{ store repo.Store }

// NewLedger returns a LedgerCounter over store.
func NewLedger(store repo.Store) *LedgerCounter { return &LedgerCounter{store: store} }

// Bump persists the increment via the repository.
func (c *LedgerCounter) Bump(ctx context.Context, id string) (int, error) {
	if err := c.store.IncrCounter(ctx, "signups", id); err != nil {
		return 0, err
	}
	return 0, nil
}
