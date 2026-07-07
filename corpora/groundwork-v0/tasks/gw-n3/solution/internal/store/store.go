// Package store is banksvc's persistence layer: an in-memory ledger whose
// BeginTx acquires a transaction that Commit or Rollback releases.
package store

// Store holds committed account balances (in minor currency units).
type Store struct{ bal map[string]int64 }

// New returns a Store with two seeded accounts.
func New() *Store { return &Store{bal: map[string]int64{"alice": 1000, "bob": 500}} }

// Balance returns an account's committed balance — a direct read, no transaction.
func (s *Store) Balance(acct string) int64 { return s.bal[acct] }

// Tx is an open transaction over a working copy of the balances.
type Tx struct {
	parent *Store
	work   map[string]int64
}

// BeginTx acquires a transaction (the obligation's acquire anchor).
func (s *Store) BeginTx() (*Tx, error) {
	w := make(map[string]int64, len(s.bal))
	for k, v := range s.bal {
		w[k] = v
	}
	return &Tx{parent: s, work: w}, nil
}

// Credit adds amt to acct within the transaction.
func (t *Tx) Credit(acct string, amt int64) error {
	t.work[acct] += amt
	return nil
}

// Commit applies the working copy and releases the transaction.
func (t *Tx) Commit() error {
	for k, v := range t.work {
		t.parent.bal[k] = v
	}
	return nil
}

// Rollback discards the working copy and releases the transaction.
func (t *Tx) Rollback() { t.work = nil }
