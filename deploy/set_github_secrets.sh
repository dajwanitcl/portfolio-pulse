#!/usr/bin/env bash
# One-shot: push every needed Actions secret from your local .env to GitHub.
# Prereqs: `brew install gh` and `gh auth login`, run from the repo root AFTER
# `gh repo create` / first push. Your .env never leaves your machine except to
# GitHub's encrypted secrets store.
set -euo pipefail

if ! command -v gh >/dev/null; then
  echo "gh CLI not found — install with: brew install gh" >&2; exit 1
fi
[ -f .env ] || { echo ".env not found — run from the project root" >&2; exit 1; }

# shellcheck disable=SC1091
set -a; source .env; set +a

for key in SUPABASE_URL SUPABASE_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID \
           ANTHROPIC_API_KEY KITE_API_KEY KITE_API_SECRET; do
  val="${!key:-}"
  if [ -n "$val" ]; then
    printf '%s' "$val" | gh secret set "$key"
    echo "set: $key"
  else
    echo "skip (empty): $key"
  fi
done
echo "Done. Verify at: repo → Settings → Secrets and variables → Actions"
