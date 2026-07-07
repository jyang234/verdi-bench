// Package wire is accountsvc's composition root.
package wire

import (
	"net/http"

	"example.com/accountsvc/internal/api"
	"example.com/accountsvc/internal/core"
	"example.com/accountsvc/internal/repo"
	"example.com/accountsvc/internal/views"
)

// Handler builds the service and returns the routed HTTP handler. signups is
// the counter behind the signup route.
func Handler(store repo.Store, signups views.Counter) http.Handler {
	svc := core.New(store, signups)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /accounts/{id}", srv.GetAccount)
	mux.HandleFunc("POST /accounts/{id}/signup", srv.Signup)
	return mux
}
