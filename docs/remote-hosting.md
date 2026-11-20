# Remote hosting over Tailscale

Optional mode: the tracker runs on an always-on machine that holds the repos, and other devices on the tailnet open a restricted viewer UI to check progress and change status. The host setup below is manual and done once.

## Design

- **Host:** an always-on machine on the tailnet with `~/GitHub`, `gh` auth, and pi. Plans are created and edited there over SSH, and agents run there, so ingestion works as in local mode (pi extension, mtime watcher, `POST /api/plans` on `127.0.0.1`). Agents on other machines cannot reach the full API; run them on the host.
- **Two listeners, one process:** the full API stays on `127.0.0.1:4747`. When `PRT_VIEWER_PORT` is set, the same process also serves the viewer app (`prt/viewer.py`) on `127.0.0.1:<PRT_VIEWER_PORT>`, as a second `uvicorn.Server` on the same event loop. Both apps share the DB connection and SSE subscriber list, so a change on either side reaches every open tab.
- **Viewer surface:** the static UI plus only:
  - `GET /api/mode` (returns `viewer`; the full app returns `full`)
  - `GET /api/plans`, `GET /api/plans/{id}`, `GET /api/plans/{id}/spec`, `GET /api/prs/{id}`, `GET /api/events`
  - `PATCH /api/prs/{id}` with only `{status, force?}`. Other fields get 422. The viewer sets `source: human` itself and stores the `Tailscale-User-Login` header, when present, in `events.actor`.
  - Nothing else is mounted: plan registration, `GET /api/prs?path&number`, `POST /api/sync`, url/branch edits, and archiving all return 404.
- **UI:** reads `/api/mode` on load. In viewer mode it hides the Sync button, the URL editor, and archiving; the plan list, PR details, status dropdown, and live updates stay.
- **Exposure:** `tailscale serve` proxies `https://<host>.<tailnet>.ts.net` to the viewer port only. Port 4747 is never served, and neither port binds off localhost. Never use `tailscale funnel`, which makes it public.
- **Data:** SQLite stays on the host, with a daily `.backup` copy kept for 14 days.

## Setup on the host

Steps for an agent with a shell on the host. Steps marked **(user)** need the Tailscale admin console; stop and ask the user to do them. The commands assume a Linux host with systemd. For a macOS host, use the launchd plist (`make install`) with `PRT_VIEWER_PORT=4748` added to its environment, and run the Tailscale CLI as `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.

1. **Check prerequisites.** Each must succeed before continuing:

   ```bash
   tailscale status            # host is logged into the tailnet
   gh auth status              # can read the tracked repos
   uv --version && node --version && git --version
   command -v pi               # agents run here
   command -v mth && pandoc --version   # renders the original plan
   ```

   Install what is missing: Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh`, then `sudo tailscale up`), `gh` (the official apt repo at https://github.com/cli/cli/blob/trunk/docs/install_linux.md, then `gh auth login`), and `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`). Node is only needed to build the UI. For `mth`, install `pandoc` and copy `~/.local/bin/mth` and `~/.config/mth/` from your main machine.

2. **Install the tracker.**

   ```bash
   git clone <pr-tracker remote> ~/GitHub/pr-tracker
   cd ~/GitHub/pr-tracker && uv sync && make build
   ```

3. **Load the pi extension on the host.** Add `"extensions": ["~/GitHub/pr-tracker/pi"]` to `~/.pi/agent/settings.json`, and copy the `pr-implement` skill and the `AGENTS.md` id-comment line from your main machine's `~/.pi/agent/`.

4. **Start the service.** The unit files live in `systemd/`:

   ```bash
   mkdir -p ~/.config/systemd/user
   cp systemd/pr-tracker.service systemd/pr-tracker-backup.service systemd/pr-tracker-backup.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now pr-tracker.service pr-tracker-backup.timer
   sudo loginctl enable-linger "$USER"     # keep user services running without a login session
   ```

   `pr-tracker.service` runs `ExecStart=%h/GitHub/pr-tracker/.venv/bin/python -m prt` (created by `uv sync`) with `Environment=PRT_VIEWER_PORT=4748` and `Restart=on-failure`, and logs to the journal (`journalctl --user -u pr-tracker`). `python -m prt` is the server entry point that starts one or both uvicorn servers. The backup timer runs daily and keeps 14 copies under `~/.local/share/pr-tracker/backups/`.

5. **Verify locally.** All must hold:

   ```bash
   curl -s 127.0.0.1:4747/api/mode                    # {"mode":"full"}
   curl -s 127.0.0.1:4748/api/mode                    # {"mode":"viewer"}
   curl -s -o /dev/null -w '%{http_code}\n' -X POST 127.0.0.1:4748/api/sync   # 404
   ss -ltn | grep -E ':474[78]'                       # both listen on 127.0.0.1 only
   ```

6. **(user) Enable HTTPS in the tailnet.** In the admin console, under DNS, turn on MagicDNS and HTTPS Certificates.

7. **Serve the viewer on the tailnet.**

   ```bash
   sudo tailscale set --operator="$USER"   # once, so serve runs without sudo
   tailscale serve --bg 4748
   tailscale serve status                  # shows https://<host>.<tailnet>.ts.net -> http://127.0.0.1:4748
   ```

   The config persists across reboots. Check that 4747 does not appear in `tailscale serve status`.

8. **(user) Restrict access.** If the tailnet has only your own devices and nothing is shared into it, the default policy is enough. Otherwise, in the access controls, give the host a tag (e.g. `tag:prt`) and allow only your user to reach port 443:

   ```json
   {"action": "accept", "src": ["<you>@<domain>"], "dst": ["tag:prt:443"]}
   ```

9. **Verify from another device.** Open `https://<host>.<tailnet>.ts.net`, change a PR's status, and confirm that (a) the change shows up through the full API on the host (`curl -s 127.0.0.1:4747/api/prs/<id>` lists an event with `source: human` and your login as `actor`), and (b) no Sync button or URL editor appears.

**Updating:** `cd ~/GitHub/pr-tracker && git pull && uv sync && make build && systemctl --user restart pr-tracker`.

**Removing the exposure:** `tailscale serve --https=443 off` (or `tailscale serve reset`), then unset `PRT_VIEWER_PORT` in the unit and restart.
