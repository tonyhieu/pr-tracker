.PHONY: dev build test

web/node_modules: web/package.json web/package-lock.json
	cd web && npm ci
	touch web/node_modules

# Server on PRT_PORT (default 4747) plus the Vite dev server proxying /api to it.
dev: web/node_modules
	trap 'kill 0' EXIT; uv run python -m prt & cd web && npm run dev

build: web/node_modules
	cd web && npm run build

test: web/node_modules
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest
	cd web && npm run typecheck
	cd web && npm run build
