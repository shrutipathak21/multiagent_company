
from __future__ import annotations

import os
import time
import threading
import traceback
import uuid
import zipfile
from pathlib import Path
import tempfile

from flask import Flask, jsonify, request, render_template, g

from ai_company.auth import require_auth
from ai_company.company import Company
from ai_company.conflict import FileStore
from ai_company.llm_free import make_free_llm
from ai_company.ratelimit import check_and_record
from ai_company.repo import extract_file_blocks, SOURCE_EXTENSIONS
from ai_company.sandbox_exec import RemoteWorkspace, SandboxUnavailable

app = Flask(__name__, static_folder="static", template_folder="templates")

RUNS: dict[str, dict] = {}
_lock = threading.Lock()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
RUNS_PER_HOUR_PER_USER = 5

@app.errorhandler(Exception)
def handle_any_error(exc):
    # Safety net: without this, an unhandled exception anywhere (a missing
    # dependency, an unexpected library error, etc.) returns Flask/Werkzeug's
    # default HTML error page. The frontend expects JSON and its `await
    # resp.json()` call throws on HTML, which was silently uncaught and left
    # the UI stuck showing "Running..." forever with no visible error. This
    # guarantees every /api/* response is parseable JSON, so the frontend's
    # existing error-display path always fires instead of hanging silently.
    app.logger.exception("Unhandled exception")
    return jsonify({"error": f"Server error: {exc}"}), 500

@app.route("/")
def index():
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not supabase_anon_key:
        return (
            "Server misconfigured: SUPABASE_URL and/or SUPABASE_ANON_KEY "
            "environment variables are not set. Add both in your hosting "
            "provider's environment variables settings and redeploy."
        ), 500
    return render_template(
        "cloud.html",
        supabase_url=supabase_url,
        supabase_anon_key=supabase_anon_key,
    )


@app.route("/api/run_repo", methods=["POST"])
@require_auth
def start_repo_run():
    user_id = g.user["id"]

    allowed, remaining = check_and_record(user_id, max_per_window=RUNS_PER_HOUR_PER_USER)
    if not allowed:
        return jsonify({
            "error": f"Rate limit reached ({RUNS_PER_HOUR_PER_USER}/hour). "
                    f"Try again later."
        }), 429

    issue = (request.form.get("issue") or "").strip()
    provider = request.form.get("provider", "groq")
    model = request.form.get("model") or None
    user_api_key = request.form.get("api_key") or None
    user_base_url = request.form.get("base_url") or None
    test_cmd = (request.form.get("test_cmd") or "").strip()
    max_fix_rounds = min(int(request.form.get("max_fix_rounds") or 1), 3)
    repo_zip = request.files.get("repo_zip")

    if not issue or not test_cmd or not repo_zip:
        return jsonify({"error": "issue, test_cmd, and repo_zip are all required"}), 400
    if provider != "mock" and provider != "ollama" and not user_api_key:
        return jsonify({"error": "api_key is required for this provider "
                                 "(bring your own — it's used for this run only, never stored)"}), 400

    repo_zip.seek(0, 2)
    size = repo_zip.tell()
    repo_zip.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify({"error": f"Repo zip too large ({size} bytes, max {MAX_UPLOAD_BYTES})"}), 400

    try:
        llm = make_free_llm(provider, model, api_key=user_api_key, base_url=user_base_url)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    run_id = uuid.uuid4().hex[:12]
    with _lock:
        RUNS[run_id] = {
            "status": "running", "user_id": user_id, "tasks": [], "log": [],
            "error": None, "round": 0, "resolved": None, "rounds": None,
            "test_output": "", "files_before": {}, "files_after": {},
        }

    file_store = _load_zip_into_filestore(repo_zip)
    RUNS[run_id]["files_before"] = {p: vf.content for p, vf in file_store.files.items()}

    threading.Thread(
        target=_execute_sandboxed_run,
        args=(run_id, llm, issue, file_store, test_cmd, max_fix_rounds),
        daemon=True,
    ).start()
    return jsonify({"run_id": run_id})

def _load_zip_into_filestore(repo_zip) -> FileStore:
    fs = FileStore()
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "repo.zip"
        repo_zip.save(zip_path)
        extract_dir = Path(tmp) / "extracted"
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                target = (extract_dir / member).resolve()
                if not str(target).startswith(str(extract_dir.resolve())):
                    raise ValueError(f"unsafe path in zip: {member}")
            zf.extractall(extract_dir)

        entries = list(extract_dir.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_dir

        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in SOURCE_EXTENSIONS:
                rel = str(path.relative_to(root))
                content = path.read_text(encoding="utf-8", errors="replace")
                fs.commit(rel, content, base_version=0, author="loader")
    return fs

def _execute_sandboxed_run(run_id, llm, issue, file_store, test_cmd, max_fix_rounds):
    remote = RemoteWorkspace(file_store)
    MAX_RUN_SECONDS = 480   # hard wall-clock cap across all rounds combined,
                            # independent of round count -- a bound of last
                            # resort in case a single LLM call somewhere
                            # stalls past its own retry/timeout logic
    started_at = time.time()
    try:
        company = Company(llm=llm, max_workers=3, verbose=False)
        company.file_store = file_store

        def apply_edits(task, output):
            for rel_path, content in extract_file_blocks(output).items():
                p = Path(rel_path)
                if p.is_absolute() or ".." in p.parts:
                    continue
                _cur, version = file_store.checkout(rel_path)
                file_store.commit(rel_path, content, base_version=version, author=task.role)

        current_issue = issue
        log: list[str] = []

        for round_no in range(1, max_fix_rounds + 2):
            if time.time() - started_at > MAX_RUN_SECONDS:
                _fail(run_id, f"Run exceeded the {MAX_RUN_SECONDS}s time limit "
                             f"partway through round {round_no}. This usually means "
                             f"an LLM provider is slow/rate-limited -- try again "
                             f"in a few minutes.")
                return
            def on_step(msg, _rn=round_no):
                with _lock:
                    if run_id in RUNS:
                        RUNS[run_id]["log"] = log + [f"round {_rn}: {msg}"]
            graph, note = company.plan_dynamically(current_issue, on_step=on_step)
            log.append(f"round {round_no}: {note}")

            from ai_company.scheduler import Scheduler
            scheduler = Scheduler(
                graph=graph, agents=company.agents,
                shared_memory=company.shared_memory, file_store=file_store,
                issue=current_issue, max_workers=3, verbose=False,
                on_task_done=apply_edits,
            )

            def on_log(message, _rn=round_no, _g=graph, _s=scheduler):
                with _lock:
                    if run_id in RUNS:
                        RUNS[run_id]["round"] = _rn
                        RUNS[run_id]["tasks"] = _serialize_graph(_g)
                        RUNS[run_id]["log"] = list(_s.timeline)
            original = scheduler._log
            scheduler._log = lambda m, _o=original, _cb=on_log: (_o(m), _cb(m))[-1]

            scheduler.run()

            remote.push_files()
            exit_code, test_output = remote.run(test_cmd)
            log.append(f"round {round_no}: tests exit={exit_code}")

            if exit_code == 0:
                _finish(run_id, True, round_no, log, test_output, file_store)
                return

            current_issue = (
                f"{issue}\n\n## Previous attempt FAILED tests\n```\n"
                f"{test_output[-2000:]}\n```\nFix the code so the tests pass."
            )

        _finish(run_id, False, max_fix_rounds + 1, log, test_output, file_store)
    except SandboxUnavailable as exc:
        _fail(run_id, f"Sandbox unavailable: {exc}")
    except Exception:
        _fail(run_id, traceback.format_exc(limit=4))
    finally:
        remote.close()

def _finish(run_id, resolved, rounds, log, test_output, file_store):
    with _lock:
        if run_id in RUNS:
            RUNS[run_id]["status"] = "finished"
            RUNS[run_id]["resolved"] = resolved
            RUNS[run_id]["rounds"] = rounds
            RUNS[run_id]["log"] = log + RUNS[run_id]["log"]
            RUNS[run_id]["test_output"] = test_output
            RUNS[run_id]["files_after"] = {p: vf.content for p, vf in file_store.files.items()}

def _fail(run_id, error_text):
    with _lock:
        if run_id in RUNS:
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["error"] = error_text

def _serialize_graph(graph) -> list[dict]:
    return [{
        "id": t.task_id, "title": t.title, "role": t.role,
        "status": t.status.value, "depends_on": t.depends_on,
        "result": (t.result or "")[:4000], "attempts": t.attempts,
    } for t in graph.tasks.values()]

@app.route("/api/status/<run_id>")
@require_auth
def status(run_id: str):
    with _lock:
        state = RUNS.get(run_id)
    if state is None:
        return jsonify({"error": "unknown run_id"}), 404
    if state["user_id"] != g.user["id"]:
        return jsonify({"error": "not your run"}), 403
    return jsonify({k: v for k, v in state.items() if k != "user_id"})

if __name__ == "__main__":
    print("AI Software Company Simulator — CLOUD (multi-tenant) -> http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
