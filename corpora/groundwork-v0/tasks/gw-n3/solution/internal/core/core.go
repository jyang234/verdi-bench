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

// BalanceView is an account's balance — a read-only snapshot.
type BalanceView struct {
	Account string
	Balance int64
}

// Balance reads an account's committed balance directly, opening no transaction.
func (s *Service) Balance(acct string) BalanceView {
	return BalanceView{Account: acct, Balance: s.store.Balance(acct)}
}
