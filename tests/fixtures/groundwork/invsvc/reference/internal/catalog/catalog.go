// Package catalog is invsvc's domain layer. It is the only layer permitted to
// call store; the handler layer must go through it.
//
// THIS IS THE REFERENCE (correct-solution) VARIANT: Describe implements the
// "show item with sibling count" feature by adding a READ-ONLY CountItems query.
// The read route ShowItem changes structure but reaches no DB write, so
// groundwork reports STRUCTURALLY-CLEAR (no new violation) — the correct way to
// add the feature, contrasted with the violating variant's audit insert.
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

// Describe reads an item's details plus the sibling count (two DB reads, no
// writes). The CountItems read is the reference feature: it keeps the read route
// read-only.
func (c *Catalog) Describe(ctx context.Context, id string) (store.Item, error) {
	var it store.Item
	if err := c.store.FetchItem(ctx, id, &it); err != nil {
		return store.Item{}, err
	}
	if _, err := c.store.CountItems(ctx); err != nil {
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
