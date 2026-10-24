# Secrets

This repo is **public**. Anything committed, even for a second and then deleted, is public forever
(forks, clones, caches). So secrets never enter the repo, encrypted or not.

## Where keys live
- On each laptop, in `.env` at the repo root. It is gitignored. Copy `.env.example`, fill it in,
  then `chmod 600 .env`. `make` loads it; `make keys` shows which are set without printing them.
- The approval key's private key lives only on the device (`~/.drawkey/private.pem` on the Pi,
  flash on the ESP32). Only its **public** key goes in `config/contract.yaml`.

## How keys reach teammates
Only the four collaborators get keys, and only by a private channel:
a direct message (Signal, iMessage, Discord DM) or a shared password-manager vault.
Never in: this repo, issues, PRs, commit messages, the shared spec doc, a team channel with
non-members, screenshots, or an AI chat.

## Guards (all on)
- `.gitignore` covers `.env`, `.env.*`, `*.pem`, `*.key`, `.drawkey/`, `wifi_secrets.h`.
- **Pre-commit hook** refuses commits that add secret files or key-shaped strings.
  Every collaborator runs `make hooks` once per clone. Never use `git commit --no-verify` here.
- **GitHub push protection** rejects pushes that contain a recognized key.

## If a key leaks
1. Revoke it at the provider immediately (Anthropic Console, Vercel, Nessie, CRS). That is the fix.
   Deleting the commit is not.
2. Make a new key, update `.env`, DM it to the team.
3. Tell the team which key leaked and where.

## After the hackathon
Revoke or rotate every key used here (Anthropic, AI Gateway, Nessie, CRS).
