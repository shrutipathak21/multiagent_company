# AI Software Company Simulator

Multi-agent orchestration engine that simulates a software company:
7 AI agent roles (PM, Tech Lead, Backend, Frontend, QA, DevOps, Security)
collaborate through a task DAG to resolve GitHub-style issues.

**The engineering core (not the agents):**

- `task_graph.py` — DAG with cycle detection (3-color DFS), status machine,
  failure propagation (FAILED cascades to BLOCKED downstream)
- `scheduler.py` — thread-pool executor: runs all dependency-satisfied tasks
  in parallel under a concurrency limit, with per-task retries (max 3) and
  rebase-retry on write conflicts
- `conflict.py` — optimistic concurrency control on a versioned file store,
  with automatic three-way merge for disjoint edits and escalation for
  overlapping ones
- `memory.py` — shared blackboard + per-agent memory, with deliberate context
  selection (issue + direct-dependency outputs + recent decisions only)
- `agents.py` — pluggable LLM backend: `MockLLM` (offline, deterministic,
  used by tests) or `AnthropicLLM` (real model)

## Run

```bash
python3 demo.py          # end-to-end run with MockLLM (no API key needed)
python3 test_engine.py   # tests: cycles, failure propagation, merges
```

To use a real model:

```python
from ai_company.company import Company
from ai_company.agents import AnthropicLLM
company = Company(llm=AnthropicLLM())   # needs ANTHROPIC_API_KEY
company.solve_issue("Add a /login endpoint with email+password auth.")
```

## v2 additions

- `planner.py` — **dynamic task graphs**: the Tech Lead LLM emits the DAG as
  JSON; parsed with fence-stripping + repair, validated (roles, deps, cycles),
  with automatic fallback to the standard workflow on any bad plan
- `repo.py` — **real repo integration**: Workspace loads a directory/git clone
  into the versioned FileStore, agents edit files via ```file:path``` blocks
  (path-traversal guarded), materialize() + run() gate resolution on the
  repo's actual tests
- `company.solve_repo_issue()` — plan → execute → run tests → feed failures
  back for up to N **fix rounds**
- `evals.py` — **eval harness**: single-agent baseline vs full company on
  the same cases; measures resolve rate, LLM calls (cost proxy), wall time

```bash
python3 demo2.py     # repo mode + eval report (ScriptedLLM, offline)
python3 test_v2.py   # planner validation, path safety, fix-round recovery
```

## v3: free LLM integration + web UI

**Free LLM backends** (`llm_free.py`) — one `OpenAICompatibleLLM` client works with any
OpenAI-compatible endpoint:
- `GroqLLM` — free cloud tier, fast (llama-3.3-70b-versatile default). Get a key at
  console.groq.com, `export GROQ_API_KEY=...`
- `OllamaLLM` — fully local, no key, no internet after model pull. `ollama pull llama3.1`,
  `ollama serve`
- Both retry on transient errors/429s with backoff, and fail with a clear actionable
  message (missing key / server unreachable) rather than hanging silently.

**Web UI** (`webapp.py` + `static/index.html`) — a local dashboard:
- Pick provider (Mock / Groq / Ollama), planning mode (standard / dynamic), describe the
  issue, hit run
- Task graph renders as a stage-by-stage board (grouped by dependency depth), color-coded
  by role, live status dots (pending/running/done/failed), click a finished card to expand
  its output
- Scheduler log streams in as it happens
- Backend runs the company in a background thread per request and exposes progress via
  polling (`/api/run`, `/api/status/<id>`) — no long-lived request, no websockets needed

```bash
pip install flask requests
python3 webapp.py
# open http://localhost:5000
```

Start with **Mock** to see the pipeline with zero setup, then switch to **Groq** (fastest
path to real output) or **Ollama** (fully offline) once you've got a key/model.

## Verifying the free LLM connection

This code was developed in a sandboxed environment whose network only allows
package registries (pypi/npm/github) — it cannot reach `api.groq.com` or a
local Ollama server, so a live model call could not be executed here. What
*was* verified in-sandbox: request formatting, retry/backoff logic, and that
both providers fail with a clear, correct error (missing key / connection
refused) rather than hanging.

Run this on your own machine first, where you have normal internet access:

```bash
export GROQ_API_KEY=your_key_here      # from console.groq.com, free
python3 live_check.py groq             # one real call, prints the raw reply

# or, fully local/offline:
ollama pull llama3.1 && ollama serve
python3 live_check.py ollama
```

If `live_check.py` prints a response, `webapp.py` and `Company(llm=GroqLLM())`
will work identically — they use the exact same `llm_free.py` client.

## v4: repo mode in the UI (user-driven, not hardcoded)

The dashboard now has two tabs:

- **Issue only** — as before, no real files.
- **Repo mode** — upload a `.zip` of any repo, describe the issue, set the test
  command, pick a provider. The company edits real files inside a sandbox
  copy, runs your real test command (`subprocess`, not simulated), and does
  fix rounds on failure — exactly the `solve_repo_issue()` path, now driven
  from the browser instead of a script. When it finishes you get:
  - a pass/fail verdict against your actual test output
  - a file-by-file diff of what changed
  - a "Download resulting repo" button (zips the sandbox)

This was tested end-to-end in-sandbox with a real zip upload of `sample_repo/`
and MockLLM: extraction, path-traversal guarding, real `pytest` execution, and
the download link all verified working. MockLLM correctly produces no code
(it doesn't emit `​```file:` blocks) and the run correctly reports failure —
proof the test-gate isn't rigged to always say pass. Swap in Groq/Ollama for
real fixes; nothing else about the flow changes.

## v5: public deployment (multi-tenant, sandboxed)

`webapp.py` stays as the local single-user dashboard — unauthenticated,
runs test commands directly via subprocess, fine on your own machine only.

For deploying publicly, use `webapp_cloud.py` instead. It adds what public
exposure actually requires:

- **Auth** (`ai_company/auth.py`) — every route requires a valid Supabase
  JWT; verified against Supabase's public JWKS endpoint (no shared
  secret to store — works with both legacy and newer Supabase key systems)
  per-request
- **Sandboxed execution** (`ai_company/sandbox_exec.py`) — test commands run
  inside an isolated, ephemeral E2B sandbox instead of `subprocess.run()` on
  the server. This replaces what would otherwise be a remote-code-execution
  hole: a public server that runs arbitrary user-supplied shell commands
  against arbitrary uploaded code. If E2B isn't configured, runs fail with a
  clear error rather than silently falling back to local execution — verified
  in testing.
- **BYO API key, never stored** (`llm_free.make_free_llm(..., api_key=...)`)
  — the browser sends the user's own Groq key per-request; it's used for
  that call only and never written to disk or a database
- **Per-user rate limiting** (`ai_company/ratelimit.py`) — 5 runs/hour/user
  by default
- **Cross-tenant isolation** — a run's status is only visible to the user
  who started it (verified: a second user's token gets a 403 on someone
  else's run_id)

Frontend: `static/cloud.html` — Supabase magic-link sign-in, then the same
upload/issue/test-command flow as the local dashboard, gated behind auth
and sending the user's own key per-request.

See `DEPLOY.md` for the full Supabase + E2B + Render setup, and
`cloud_check.py` for verifying E2B connectivity before going live.

**Verified in this environment** (no live network to Supabase/E2B/Render
available here, so these are what could be checked locally): JWT
verification correctly accepts valid / rejects expired / rejects forged /
rejects missing tokens; rate limiter correctly enforces per-user windows;
the full HTTP flow (auth → validation → mock run → E2B-unavailable failure)
end to end, including confirming it fails safe rather than falling back to
unsafe execution; cross-tenant status isolation. **Not verified** (needs your
own accounts/keys): live Supabase auth, live E2B sandbox execution, live
Render deployment — `cloud_check.py` and a real sign-in are the way to check
those yourself.

## Multiple LLM providers

Both dashboards now support choosing from several providers, not just Groq:

- **Groq** — free tier, fast
- **Gemini** — free tier via Google's OpenAI-compatible endpoint
  (`https://generativelanguage.googleapis.com/v1beta/openai`), get a key at
  aistudio.google.com/apikey
- **Ollama** — fully local, no key
- **Custom** — any other OpenAI-compatible API (Together.ai, Fireworks,
  OpenRouter, Cerebras, a self-hosted vLLM/LM Studio server, etc.) — supply
  the base URL and model name yourself
- **Mock** — offline, no real calls

All of these share one client (`llm_free.OpenAICompatibleLLM`), so adding
another OpenAI-compatible provider in the future is a ~10-line subclass, not
a rewrite. On the cloud dashboard, provider/model/key/base_url are all
supplied per-request by the user (never stored). On the local dashboard,
Groq/Gemini keys come from `GROQ_API_KEY`/`GEMINI_API_KEY` env vars (matching
the existing local pattern), and Custom falls back to `CUSTOM_API_KEY` if not
typed into the field.
