// Package catalog is invsvc's domain layer. It is the only layer permitted to
// call store; the handler layer must go through it. Rename deliberately performs
// two DB writes (the rename plus an audit insert) so the per-route I/O budget
// fitness function has something to measure. Describe is read-only — that is the
// invariant ShowItem's must_not_reach rule guards.
package catalog

import (
	"context"

	"example.com/invsvc/internal/store"
)

// Catalog holds the domain logic.
type Catalog struct {
	store *store.Store
}

// New returns a Catalog over st.
func New(st *store.Store) *Catalog { return &Catalog{store: st} }

// Describe reads an item's details (one DB read, no writes).
func (c *Catalog) Describe(ctx context.Context, id string) (store.Item, error) {
	var it store.Item
	if err := c.store.FetchItem(ctx, id, &it); err != nil {
		return store.Item{}, err
	}
	return it, nil
}

// Rename renames an item and records an audit entry (two DB writes).
func (c *Catalog) Rename(ctx context.Context, id, name string) error {
	if err := c.store.SaveName(ctx, id, name); err != nil {
		return err
	}
	return c.store.AppendAudit(ctx, id)
}
