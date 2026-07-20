# Problem: Local code not pushed to viewer's GitHub fork

## What happened
The viewer (mrt14august) forked the repo but their fork only has `setup_check.yml`.
The other 3 workflows (`fast_poll.yml`, `dma_scan.yml`, `morning_auth.yml`) are missing
from their fork because the local repo is connected to the original remote, not their fork.

## Current state
- Local repo remote: `https://github.com/investorswaybz-lang/portfolio-pulse.git`
- Viewer's fork: `https://github.com/mrt14august/portfolio-pulse.git`
- Local has all files. Viewer's fork is behind.

## Fix needed
Run these commands in Terminal from the Portfolio Pulse folder:

```bash
cd "/Users/siddharthagarwal/Desktop/Portfolio Pulse"
git remote add myfork https://github.com/mrt14august/portfolio-pulse.git
git push myfork main
```

When asked for password, use a GitHub Personal Access Token (not the real password):
github.com → profile → Settings → Developer settings → Personal access tokens
→ Tokens (classic) → Generate new token → tick "repo" → copy token → paste as password

## After pushing
1. Go to github.com/mrt14august/portfolio-pulse → Actions tab
2. You should now see all 4 workflows listed
3. Enable dma_scan, fast_poll, morning_auth (they default to disabled on forks)
4. fast_poll runs on a schedule — it will process /add commands automatically
5. Test by sending /add RELIANCE to the bot and waiting ~10 min

## What already works
- Setup check: green
- Telegram connection: working (bot sends messages)
- Supabase: connected
- RELIANCE was successfully added to watchlist at 11:48 AM
