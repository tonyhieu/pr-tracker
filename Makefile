.PHONY: dev build test install rss

LABEL := com.avo.pr-tracker
PLIST := $(HOME)/Library/LaunchAgents/$(LABEL).plist

web/node_modules: web/package.json web/package-lock.json
	cd web && npm ci
	touch web/node_modules

# Server on PRT_PORT (default 4747) plus the Vite dev server proxying /api to it.
dev: web/node_modules
	trap 'kill 0' EXIT; uv run python -m prt & cd web && npm run dev

build: web/node_modules
	cd web && npm run build

# Run the server at login as a launchd user agent pointing at this checkout.
install: build
	uv sync
	sed -e 's|@REPO@|$(CURDIR)|g' -e 's|@HOME@|$(HOME)|g' launchd/$(LABEL).plist > $(PLIST)
	launchctl bootout gui/$$(id -u)/$(LABEL) 2>/dev/null || true
	# bootout returns before the old process exits; bootstrap fails with error 5 until it does
	while launchctl print gui/$$(id -u)/$(LABEL) >/dev/null 2>&1; do sleep 0.2; done
	launchctl bootstrap gui/$$(id -u) $(PLIST)

# Resident memory of the running agent, in KB (target: under 40 MB at idle).
rss:
	@pid=$$(launchctl print gui/$$(id -u)/$(LABEL) 2>/dev/null | awk '/^\tpid = /{print $$3}'); \
	if [ -n "$$pid" ]; then ps -o rss= -p $$pid; else echo "$(LABEL) is not running"; exit 1; fi

test: web/node_modules
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest
	cd web && npm run typecheck
	cd web && npm run build
	node --test pi/hooks.test.ts
