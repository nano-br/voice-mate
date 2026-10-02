"""Engine side of the daemon API v2 (docs/companion-app.md).

Building blocks with no engine dependency (stdlib, `app.protocol`, `app.i18n`):
the event journal behind `GET /events`, the client registry with its exclusive
leases, delivery tracking for published results, the bearer token, the process
lifecycle (readiness and shutdown) and the HTTP server. Import the modules
directly: this package deliberately re-exports nothing, so `app.core` can use
the hub without pulling in the HTTP server.
"""
