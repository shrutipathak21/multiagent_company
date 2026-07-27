# Deploying publicly (free tier)

This deploys `webapp_cloud.py` — the multi-tenant, auth-gated,
sandboxed version. Do NOT deploy `webapp.py` publicly; it has no auth
and runs test commands directly on the server.

Everything the app needs is set via environment variables on your host
(Render) — you never need to hand-edit any file in this project.

## What you're setting up

| Piece | Service | Why |
|---|---|---|
| Auth | Supabase (free) | Handles signup/login; we verify sessions via Supabase's public JWKS endpoint |
| Sandboxed execution | E2B (free tier) | Isolated VM per test run — the fix for the RCE risk |
| Hosting | Render.com (free web service) | Runs webapp_cloud.py publicly |
| Uptime | GitHub Actions (free) | Keeps Supabase from pausing due to inactivity |

Each user brings their own Groq API key (free, from console.groq.com) —
you don't pay for their usage.

## 1. Supabase (auth)

1. Create a free project at supabase.com
2. Settings → API → copy the **Project URL** and the **Publishable key**
   (`sb_publishable_...`) — if your project still shows older naming,
   use **anon public** instead
3. In Supabase: Authentication → Providers → enable **GitHub** (needs a
   GitHub OAuth App — see step 2 below) and/or **Email** (magic link,
   on by default, but limited to 2 emails/hour unless you configure
   custom SMTP — GitHub sign-in avoids this entirely and is recommended)

You do **not** need a "JWT Secret." The backend verifies sign-ins via
Supabase's public JWKS endpoint, so there's no shared secret to find or
leak.

## 2. GitHub OAuth (recommended sign-in method)

1. GitHub → Settings → Developer settings → OAuth Apps → New OAuth App
2. Homepage URL: your Render URL (you'll get this in step 4, can add
   it after)
3. Authorization callback URL: `https://YOUR_PROJECT.supabase.co/auth/v1/callback`
4. Register, copy the **Client ID**, generate and copy a **Client Secret**
5. Supabase → Authentication → Providers → GitHub → paste both in → Save

## 3. E2B (sandboxed execution)

1. Create a free account at e2b.dev
2. Copy your API key from the dashboard

## 4. Push the project to your own GitHub repo

Push this whole `company/` folder — including
`.github/workflows/keep-alive.yml` — to a GitHub repo you control.
This is what Render deploys from, and what runs the keep-alive job.

**Why keep-alive matters:** free-tier Supabase projects pause after 7
days of no API activity. `.github/workflows/keep-alive.yml` pings
Supabase every 3 days via GitHub's free scheduled Actions so this
never happens on its own.

It needs two repo secrets: repo → Settings → Secrets and variables →
Actions → New repository secret:
```
SUPABASE_URL       = https://YOUR_PROJECT.supabase.co
SUPABASE_ANON_KEY  = your_publishable_or_anon_key
```

## 5. Deploy to Render (free tier)

1. New → Web Service → connect your GitHub repo
2. Settings:
   - **Build command:** `pip install -r requirements-cloud.txt`
   - **Start command:** `gunicorn -w 1 --timeout 120 -b 0.0.0.0:$PORT webapp_cloud:app`
     (`-w 1`: see the note above about in-memory run state. `--timeout 120`:
     gunicorn's default 30-second worker timeout can kill a request while
     it's still receiving an uploaded zip file, especially on a free-tier
     instance or a slow connection — 120s gives uploads real breathing
     room. The actual agent work happens in a background thread *after*
     the request returns, so it was never subject to this timeout anyway;
     this only affects the brief window while the upload itself transfers)
     (must be exactly 1 worker — run state is tracked in memory, and
     gunicorn workers are separate processes that don't share memory;
     with more than 1 worker, polling a run's status can land on a
     different worker than the one that started it, causing
     "unknown run_id" errors. Fine for low traffic; if you outgrow a
     single worker, move run state to Supabase/Redis instead of memory)
   - **Instance type:** Free
3. Environment variables — this is the only place any of these values
   need to go:
   ```
   SUPABASE_URL       = https://YOUR_PROJECT.supabase.co
   SUPABASE_ANON_KEY  = your_publishable_or_anon_key
   E2B_API_KEY        = your_e2b_key
   ```
4. Deploy. Render gives you a URL like `https://your-app.onrender.com`.
   This URL doesn't expire — the free instance just sleeps after 15 min
   idle and wakes on the next request (~30-60s cold start).

The homepage now renders `SUPABASE_URL`/`SUPABASE_ANON_KEY` from these
environment variables automatically — no code file needs editing, on
this deploy or any future one. If you rotate a key, just update it in
Render's environment settings and redeploy.

## 6. Verify before telling anyone about it

```bash
pip install e2b-code-interpreter
export E2B_API_KEY=your_key
python3 cloud_check.py
```

This confirms E2B is actually reachable and sandboxed execution works.
If this fails, the deployed app correctly refuses to run anything
rather than falling back to something unsafe — but you want to know
that *before* a user hits it.

Then open your Render URL, sign in with GitHub, and run a small test
repo through it end to end.

## Honest limitations of this free-tier setup

- **Render's free web service URL does not expire** — it just sleeps
  after 15 min idle, ~30-60s cold start on the next request.
- **Supabase free projects pause after 7 days of total inactivity** —
  the keep-alive workflow handles this.
- **Email sign-in is capped at 2/hour on Supabase's default sender**
  and its test domain can only email your own account owner address —
  GitHub sign-in avoids this limitation entirely, which is why it's
  the recommended default here.
- **Rate limiting is in-memory** (`ratelimit.py`) and resets on
  service restart/sleep-wake. Good enough to stop casual abuse, not
  airtight.
- **5MB upload cap and a 90-second sandbox execution cap** are
  hardcoded in `webapp_cloud.py` — conservative defaults for a free
  E2B tier.
- **No live GitHub repo connection for user repos** — upload a zip,
  download a zip. No PR creation.
- **No payment/billing layer.** Users bring their own LLM key, so
  you're not on the hook for their costs, but this isn't a monetized
  product as built.

## Changelog of real bugs found and fixed during first live deployment

Each of these was found by actually deploying and hitting it, not anticipated in advance — kept here so a future contributor understands why the code looks the way it does:

1. **Missing `cryptography` dependency** — `PyJWT`'s ES256 verification needs it; wasn't pinned in `requirements-cloud.txt`, worked locally only because it happened to already be present in dev.
2. **`gunicorn -w 2` broke in-memory run tracking** — separate worker processes don't share memory, so polling a run's status could land on a worker that never saw it. Fixed to `-w 1`; documented as a scaling limit if traffic grows.
3. **E2B SDK `Sandbox()` deprecated** — current SDK requires `Sandbox.create()`; the bare constructor now expects internal connection args it never receives on its own.
4. **E2B `CommandExitException` on non-zero exit** — `.commands.run()` raises instead of returning a normal result on any non-zero exit code, unlike `subprocess.run()`. A failing test run (a completely normal outcome) was being treated as a crash. Fixed by catching the exception, which conveniently carries `.exit_code`/`.stdout`/`.stderr` itself.
5. **gunicorn's default 30s timeout killed file uploads** — increased to `--timeout 120`.
6. **`const supabase = ...` collided with the CDN's global `window.supabase`** — caused a page-wide `SyntaxError` that silently broke every button on the page. Renamed to `supabaseClient`.
7. **Any unhandled backend exception returned a raw HTML error page** — the frontend's `resp.json()` call then threw, uncaught, leaving the UI stuck showing "Running..." forever with zero visible error. Added a global Flask error handler (`@app.errorhandler(Exception)`) so `/api/*` responses are always valid JSON, plus frontend-side `try/catch` around every `resp.json()` call as a second layer.
8. **Planning phase (Product Manager + Tech Lead calls) had zero progress visibility** — happens before the Scheduler object exists, so a slow or stuck call there looked identical to a true hang. Added an `on_step` callback so each step logs before it blocks.
9. **LLM retry defaults were too patient for a synchronous UI wait** — 3 retries × 60s timeout meant a single stuck call could silently retry for ~3 minutes before ever surfacing an error. Tightened to 2 retries × 30s (~1 minute worst case).
10. **No overall wall-clock cap on a run** — bounded only by round count, which doesn't bound total time if individual calls are slow. Added a hard 480-second cap across all rounds combined, as a backstop independent of everything else.
11. **Unbounded 429 retry-after wait** — on a rate-limit response, the code trusted the provider's `retry-after` header with no upper bound. If Groq (or any provider) returns a large value under heavy load, this could silently sleep for many minutes with zero visible progress — very likely the actual cause of a multi-minute "frozen" stall during testing. Capped to 15s max wait, and the resulting error now explicitly says "rate limited" instead of a generic failure.
12. **Agents were never actually told the required file-output format** — prompts said "output complete, runnable code files" but never specified the `​```file:path` fence syntax our parser requires, so models defaulted to standard markdown fences that were silently discarded. Real symptom: all tasks report DONE, but "Files changed" shows 0 files written. Fixed by (a) adding explicit instructions with a concrete example to every code-writing role's prompt, and (b) adding two fallback parsers for near-miss formats (bare path as the fence's language tag, or a file path mentioned as a comment/label directly above a standard fence) so a model's near-correct output isn't silently discarded even if it doesn't follow the exact syntax.
