# PR tracker

A local server that tracks every active PR plan and the status of each PR in it. Agents update it over HTTP and through the pi extension in `pi/`; the React UI in `web/` updates live over SSE.

- `make dev`: the server on `PRT_PORT` (default 4747) plus the Vite dev server.
- `make test`: lint, format check, pytest, type check, UI build, extension tests.
- `make install`: build the UI and run the server at login as a launchd user agent.

To host it on an always-on machine and open a viewer-only UI from other devices on your tailnet, see [docs/remote-hosting.md](docs/remote-hosting.md).
