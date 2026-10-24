# Approval key on a Raspberry Pi

Same job and contract as `firmware/` (see `docs/device-api.md`). Pins are in `config.toml` (BCM numbers):

```
GPIO17 ── approve button ── GND      GPIO22 ── resistor ── green LED ── GND
GPIO27 ── reject button  ── GND      GPIO23 ── resistor ── red LED   ── GND
```

## Setting up the Pi

1. Flash Raspberry Pi OS (Bookworm) with Raspberry Pi Imager. In its settings: hostname `drawkey`, your user, Wi-Fi, enable SSH.
2. `ssh <user>@drawkey.local`
3. `git clone <repo url> draw && cd draw && git checkout <branch>` (or `rsync -a --exclude .venv ./ <user>@drawkey.local:draw/` from the Mac).
4. `sudo ./device/setup_pi.sh` — installs the venv + deps, Tailscale, and the `drawkey` systemd service (starts on boot, port 8000, restarts on crash). It ends by printing the public key.
5. `sudo tailscale up` → open the printed URL and log into the team tailnet. `tailscale ip -4` gives the Pi's address.
6. Check it: `systemctl status drawkey`, logs with `journalctl -u drawkey -f`. From the Mac: `uv run firmware/check_device.py <tailscale-ip>:8000`.
7. **Pubkey hand-off:** `curl http://<tailscale-ip>:8000/pubkey` and send the hex to A. A replaces the value of `esp32_pubkey:` in `config/contract.yaml` with it and commits. (The field keeps its name; the executor doesn't care which device signs.)
8. Back up `~/.drawkey/private.pem` somewhere off the repo (USB stick, password manager). If it's lost, a new key is generated and step 7 has to be redone. Never commit it.
9. Updating code later: `git pull && sudo systemctl restart drawkey`.

**Buttons only.** The service sets `DRAWKEY_BUTTONS_ONLY=1`, because the page can be reached through Tailscale Funnel. In that mode the page at `/` shows the waiting payment (payee, amount, job) read-only and says "Press the green or red button on the key."; `POST /decide/approve` and `/decide/reject` return 403. Only the physical buttons decide. A Pi set up before this needs `sudo ./device/setup_pi.sh` again (or add `Environment=DRAWKEY_BUTTONS_ONLY=1` to `/etc/systemd/system/drawkey.service`, then `sudo systemctl daemon-reload && sudo systemctl restart drawkey`). Without the variable (`make device`), the page keeps its Approve/Reject buttons.

On a Mac: `make device` (port 8001, so it runs beside `make api` on 8000), then type `a` + Enter to approve, `r` + Enter to reject. LED changes print.
The first run makes `~/.drawkey/private.pem` (mode 600). It never leaves the machine; back it up or the pubkey in `config/contract.yaml` changes.

Test: `make device-test`. Against a running device: `uv run firmware/check_device.py localhost:8001`.
