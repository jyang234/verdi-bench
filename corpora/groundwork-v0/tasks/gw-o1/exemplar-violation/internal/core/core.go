// Package core is walletsvc's domain layer.
package core

import "example.com/walletsvc/internal/store"

// Service holds the domain logic.
type Service struct {
	store *store.Store
}

// New returns a Service over st.
func New(st *store.Store) *Service { return &Service{store: st} }

// Balance returns an account's committed balance.
func (s *Service) Balance(acct string) int64 { return s.store.Balance(acct) }

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

// Transfer moves amt from one account to another within a transaction.
func (s *Service) Transfer(from, to string, amt int64) error {
	tx, err := s.store.BeginTx()
	if err != nil {
		return err
	}
	if err := tx.Debit(from, amt); err != nil {
		return err
	}
	if err := tx.Credit(to, amt); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit()
}
