// Package wire is limitsvc's composition root.
package wire

import (
	"net/http"

	"example.com/limitsvc/internal/api"
	"example.com/limitsvc/internal/core"
	"example.com/limitsvc/internal/store"
)

// Handler builds the service over lim and returns the routed HTTP handler.
func Handler(lim *store.Limiter) http.Handler {
	svc := core.New(lim)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /run", srv.Run)
	mux.HandleFunc("POST /process", srv.Process)
	return mux
}
