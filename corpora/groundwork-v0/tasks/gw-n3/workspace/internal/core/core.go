// Package core is banksvc's domain layer.
package core

import "example.com/banksvc/internal/store"

// Service holds the domain logic.
type Service struct {
	store *store.Store
}

// New returns a Service over st.
func New(st *store.Store) *Service { return &Service{store: st} }

// Deposit credits an account within a transaction, releasing it on every path.
func (s *Service) Deposit(acct string, amt int64) error {
	tx, err := s.store.BeginTx()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := tx.Credit(acct, amt); err != nil {
		return err
	}
	return tx.Commit()
}
