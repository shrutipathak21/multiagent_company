# AI Software Company Simulator — Project Summary

## What it is

A multi-agent orchestration engine that simulates a small software company.
Given a GitHub-style issue, seven distinct AI agents — Product Manager, Tech
Lead, Backend Engineer, Frontend Engineer, QA, DevOps, and Security —
collaborate through a shared task graph to design, implement, test, and
review a fix, exactly as a real engineering team would hand work between
roles.

The project's premise is that the agents themselves are the easy part. The
hard part — and the part this project is actually about — is the systems
layer that makes multiple independent, sometimes-wrong AI workers cooperate
without corrupting each other's work: task scheduling, shared memory,
conflict resolution, and failure recovery.

## Why it exists

Built as a portfolio project to demonstrate systems-engineering ability
(concurrency, conflict resolution, dependency graphs, fault tolerance)
using LLM orchestration as the application domain — positioned primarily
for SDE roles, with a secondary AI/ML angle once it's evaluated
empirically rather than just demoed.

## Architecture

```
              product_manager
                     |
                 tech_lead
                /         \
          backend         frontend      (run in parallel)
                \         /
             +----+-------+----+
             |                 |
            qa             security     (run in parallel)
             \                /
                  devops
```

Agents don't talk to each other directly. All coordination goes through
three shared systems: the task graph (what to do and in what order), the
memory layer (what's been decided so far), and the file store (what's been
written so far). That separation keeps every task replayable from its
inputs alone, which is what makes debugging a multi-agent run tractable.

## Core engineering components

**Task graph (`task_graph.py`)**
A DAG with a proper status machine (PENDING → READY → RUNNING → DONE /
FAILED / BLOCKED). Cycle detection uses 3-color DFS, so a malformed or
LLM-generated plan that accidentally creates a circular dependency is
caught before anything runs. Failure propagation is automatic: if a task
fails permanently, everything downstream is cascaded to BLOCKED rather
than hanging forever or crashing the run.

**Scheduler (`scheduler.py`)**
A `ThreadPoolExecutor`-based scheduler that repeatedly asks the graph which
tasks are ready and runs all of them concurrently, up to a worker limit —
so independent work (e.g. backend and frontend implementation) genuinely
executes in parallel rather than sequentially. Failed tasks retry up to 3
times before being marked FAILED; write conflicts (see below) trigger a
rebase-and-retry rather than an outright failure.

**Conflict resolution (`conflict.py`)**
The centerpiece of the "engineering, not just prompting" argument. Files
are versioned with optimistic concurrency control, the same mechanism
databases and Git use: an agent checks out a file's version, and its write
only succeeds if nobody else has committed since. On conflict, a
simplified three-way merge (via `difflib`) automatically reconciles
disjoint edits; only genuinely overlapping edits are escalated back to the
scheduler for a rebase retry. Tested against both cases explicitly:
disjoint edits merge cleanly, same-line edits correctly refuse to
auto-merge.

**Memory (`memory.py`)**
Two layers: a shared "blackboard" every agent can read (decisions,
artifacts) and private per-agent memory. Context sent to each agent is
deliberately curated — the original issue, only the outputs of tasks it
directly depends on, and recent shared decisions — rather than dumping the
full run history into every prompt.

**Dynamic planning (`planner.py`)**
Initially the workflow above was hardcoded. This module lets the Tech
Lead agent generate the task graph itself as JSON, which is then parsed
defensively: fence-stripping, JSON-repair on malformed output, and
validation of roles/dependencies/cycles, with automatic fallback to the
standard workflow (and a logged reason) if the model's plan doesn't
parse. The system is designed to never crash because a model rambled.

**Real repo integration (`repo.py`)**
Connects the simulator to actual codebases instead of toy in-memory
strings. A `Workspace` loads a directory or git clone into the versioned
file store (so the conflict-resolution machinery applies to real code),
agents emit changes as fenced ` ```file:path ` blocks (path-traversal
attacks are explicitly rejected), and `materialize()` + `run()` write the
result to disk and execute the repo's actual test suite via `subprocess`.
Resolution is defined as "the real tests pass," not "an agent said it's
done."

**Fix rounds (`Company.solve_repo_issue`)**
When tests fail, the failure output is fed back into the next planning
cycle for up to N rounds, so the system can iterate rather than stop at
the first attempt — mirroring how a human developer would respond to a
failing CI run.

**Eval harness (`evals.py`)**
Runs the same issue against two conditions — a single generic agent
solving it in one shot, versus the full multi-agent company — and reports
resolve rate, LLM call count (a cost proxy), and wall time for each. This
is what turns the project from a demo into a claim that can be backed by
a number: does the multi-agent structure actually help, and at what cost.

## Free LLM integration

`llm_free.py` provides one OpenAI-compatible HTTP client with retry and
backoff, with two zero-cost presets:
- **Groq** — free-tier cloud inference (`llama-3.3-70b-versatile` default),
  needs only a free API key
- **Ollama** — fully local and free, no key, no internet required after
  pulling a model

Both fail with clear, actionable errors (missing key, unreachable server)
rather than hanging. A standalone `live_check.py` script lets a user
verify their provider works with a single real API call before trusting a
full multi-agent run to it.

## Web UI

A local Flask dashboard (`webapp.py` + `static/index.html`, vanilla
HTML/CSS/JS, no frontend framework or build step) with two modes:

- **Issue only** — visualizes the task graph as a stage-by-stage board
  (grouped by dependency depth), color-coded by agent role, with live
  status dots and a streaming scheduler log. Runs execute in a background
  thread per request; the UI polls for progress rather than blocking or
  requiring websockets.
- **Repo mode** — the interactive, non-hardcoded path. A user uploads any
  repo as a `.zip`, writes their own issue description, sets their own
  test command, and picks a provider. The company edits real files, runs
  the user's real test command, performs fix rounds on failure, and
  surfaces a pass/fail verdict, a file-by-file diff, and a downloadable
  zip of the result.

## What's been verified vs. what depends on external factors

Verified directly, independent of any specific LLM's output quality:
- Cycle detection, failure propagation, and retry logic (unit tests)
- Disjoint-edit auto-merge and overlapping-edit conflict detection (unit
  tests, including a same-line-insertion adversarial case)
- Dynamic plan parsing against malformed/fenced/prose-wrapped LLM output,
  including rejection of unknown roles, missing dependencies, and cycles
- Path-traversal rejection in both agent-authored file blocks and
  uploaded zip archives
- The full repo-mode HTTP flow end to end — real zip upload, real
  extraction, real file store, real `pytest` execution, real downloadable
  output — using `MockLLM` to confirm the pipeline is honest: it correctly
  reports failure when no real fix is produced, rather than always
  reporting success
- A fix-round recovery scenario: a scripted first-round failure is
  correctly fed back and resolved in round two

Not yet verified (requires the user's own network access, unavailable in
the development sandbox used to build this): actual code-generation
quality from a live model (Groq or Ollama). The client code, error
handling, and the entire orchestration/conflict/test-gating pipeline
around it are proven; only the "does a real LLM reliably write a correct
fix" link in the chain is still open, and is meant to be checked locally
via `live_check.py` before a full run.

## Tech stack

| Layer | Technology | Rationale |
|---|---|---|
| Core engine | Python 3.12, standard library only | `dataclasses`/`enum` for the task graph, `concurrent.futures.ThreadPoolExecutor` for the scheduler (LLM calls are I/O-bound, so threads are the right concurrency primitive, not asyncio or multiprocessing), `difflib.SequenceMatcher` for three-way merges, `threading.Lock` for shared-state safety |
| LLM backends | `requests`, hand-rolled OpenAI-compatible client | One class serves Groq, Ollama, and any other OpenAI-compatible endpoint, rather than depending on separate provider SDKs |
| Repo integration | `subprocess`, `pathlib`, `shutil` | Sandboxed repo copies, real test-suite execution as the resolution gate |
| Web backend | Flask, background threads, in-memory state | One thread per run, polling-based progress instead of websockets — appropriately lightweight for a single-user local dashboard |
| Web frontend | Vanilla HTML/CSS/JS | No framework or build step; `fetch` + `setInterval` polling, Space Grotesk + JetBrains Mono (Google Fonts) for typography |
| Testing | Plain `assert`-based scripts | Deliberately dependency-light so the test suite runs anywhere without a test framework |

## Suggested next steps

1. Run `live_check.py` against Groq, then run the eval harness for real
   across a small set of issues to get an actual resolve-rate and
   cost number — this is the evidence that would make the project count
   for AI/ML roles, not just SDE ones.
2. Add a role-ablation flag (e.g. drop QA or Security) to the eval harness
   to measure which agent roles actually contribute to resolve rate,
   upgrading the project from "built a system" to "measured what matters."
3. Consider persisting run state (currently in-memory only) if the
   dashboard needs to survive a server restart or be shared beyond
   localhost.
