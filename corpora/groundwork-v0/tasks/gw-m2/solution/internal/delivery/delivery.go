// Package delivery holds the receipt-recording seam. Deliverer has two
// implementations; main wires exactly one live. A caller cannot tell from the
// interface alone whether Record touches the database — only the wired
// implementation decides that.
package delivery

import (
	"context"

	"example.com/inboxsvc/internal/repo"
)

// Deliverer records that a message was acted on (delivered, or read).
type Deliverer interface {
	Record(ctx context.Context, id string) error
}

// MemDeliverer keeps records in process memory (no external effect).
type MemDeliverer struct{ seen map[string]int }

// NewMem returns an empty MemDeliverer.
func NewMem() *MemDeliverer { return &MemDeliverer{seen: map[string]int{}} }

// Record notes the message id in memory.
func (d *MemDeliverer) Record(_ context.Context, id string) error {
	d.seen[id]++
	return nil
}

// DbDeliverer persists a receipt through the repository (a DB write per Record).
type DbDeliverer struct{ store repo.Store }

// NewDb returns a DbDeliverer over store.
func NewDb(store repo.Store) *DbDeliverer { return &DbDeliverer{store: store} }

// Record persists a receipt via the repository.
func (d *DbDeliverer) Record(ctx context.Context, id string) error {
	return d.store.InsertReceipt(ctx, id)
}
