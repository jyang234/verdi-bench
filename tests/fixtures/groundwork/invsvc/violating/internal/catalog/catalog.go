// Package catalog is invsvc's domain layer. It is the only layer permitted to
// call store; the handler layer must go through it.
//
// THIS IS THE TRAP-VIOLATING VARIANT: Describe (reached from the read route
// ShowItem) now ALSO appends an audit row — a natural-looking "log every view"
// feature whose implementation makes the read route reach a DB INSERT, breaking
// the must_not_reach invariant. The base (clean) graph has no such edge, so
// groundwork verify reports it as a NEW violation.
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

// Describe reads an item's details and records that the item was viewed. The
// audit insert is the planted violation: it makes the read route ShowItem reach
// a DB INSERT.
func (c *Catalog) Describe(ctx context.Context, id string) (store.Item, error) {
	var it store.Item
	if err := c.store.FetchItem(ctx, id, &it); err != nil {
		return store.Item{}, err
	}
	_ = c.store.AppendAudit(ctx, id)
	return it, nil
}

// Rename renames an item and records an audit entry (two DB writes).
func (c *Catalog) Rename(ctx context.Context, id, name string) error {
	if err := c.store.SaveName(ctx, id, name); err != nil {
		return err
	}
	return c.store.AppendAudit(ctx, id)
}
