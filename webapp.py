
from __future__ import annotations

import shutil
import tempfile
import threading
import traceback
import uuid
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, send_file

from ai_company.company import Company
from ai_company.llm_free import make_free_llm
from ai_company.repo import Workspace

app = Flask(__name__, static_folder="static")


@app.errorhandler(Exception)
def handle_any_error(exc):
    # Same reasoning as webapp_cloud.py: guarantees /api/* responses are
    # always parseable JSON, so the frontend's error-display path fires
    # instead of the UI hanging on an unparseable raw error page.
    app.logger.exception("Unhandled exception")
    return jsonify({"error": f"Server error: {exc}"}), 500


RUNS: dict[str, dict] = {}
WORKDIR = Path(tempfile.gettempdir()) / "ai_company_runs"
WORKDIR.mkdir(exist_ok=True)
_lock = threading.Lock()

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/run", methods=["POST"])
def start_run():
    body = request.get_json(force=True)
    issue = (body.get("issue") or "").strip()
    provider = body.get("provider", "mock")
    model = body.get("model") or None
    mode = body.get("mode", "standard")

    if not issue:
        return jsonify({"error": "issue is required"}), 400
    try:
        llm = make_free_llm(provider, model)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    run_id = uuid.uuid4().hex[:12]
    with _lock:
        RUNS[run_id] = _new_state(issue, provider, mode)

    threading.Thread(target=_execute_issue_run, args=(run_id, llm, issue, mode),
                     daemon=True).start()
    return jsonify({"run_id": run_id})

def _execute_issue_run(run_id: str, llm, issue: str, mode: str) -> None:
    try:
        company = Company(llm=llm, max_workers=3, verbose=False)
        from ai_company.scheduler import Scheduler

        if mode == "dynamic":
            graph, note = company.plan_dynamically(issue)
            _push(run_id, log_line=f"planner: {note}")
        else:
            graph = company._build_standard_workflow(issue)

        scheduler = Scheduler(
            graph=graph, agents=company.agents,
            shared_memory=company.shared_memory, file_store=company.file_store,
            issue=issue, max_workers=3, verbose=False,
        )
        _attach_live_polling(run_id, graph, scheduler)
        scheduler.run()

        with _lock:
            RUNS[run_id]["status"] = "finished"
            RUNS[run_id]["tasks"] = _serialize_graph(graph)
            RUNS[run_id]["log"] = scheduler.timeline
    except Exception:
        _fail(run_id)

@app.route("/api/run_repo", methods=["POST"])
def start_repo_run():
    issue = (request.form.get("issue") or "").strip()
    provider = request.form.get("provider", "mock")
    model = request.form.get("model") or None
    base_url = request.form.get("base_url") or None
    test_cmd = (request.form.get("test_cmd") or "").strip()
    max_fix_rounds = int(request.form.get("max_fix_rounds") or 2)
    repo_zip = request.files.get("repo_zip")

    if not issue or not test_cmd or not repo_zip:
        return jsonify({"error": "issue, test_cmd, and repo_zip are all required"}), 400
    try:
        llm = make_free_llm(provider, model, base_url=base_url)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    run_id = uuid.uuid4().hex[:12]
    run_dir = WORKDIR / run_id
    upload_dir, source_dir, sandbox_dir = run_dir / "upload", run_dir / "source", run_dir / "sandbox"
    upload_dir.mkdir(parents=True)

    zip_path = upload_dir / "repo.zip"
    repo_zip.save(zip_path)
    try:
        _safe_extract(zip_path, source_dir)
    except Exception as exc:
        shutil.rmtree(run_dir, ignore_errors=True)
        return jsonify({"error": f"could not extract zip: {exc}"}), 400

    entries = list(source_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        source_dir = entries[0]

    with _lock:
        RUNS[run_id] = _new_state(issue, provider, "repo")
        RUNS[run_id]["repo_mode"] = True
        RUNS[run_id]["sandbox_dir"] = str(sandbox_dir)

    threading.Thread(
        target=_execute_repo_run,
        args=(run_id, llm, issue, str(source_dir), str(sandbox_dir), test_cmd, max_fix_rounds),
        daemon=True,
    ).start()
    return jsonify({"run_id": run_id})

def _execute_repo_run(run_id, llm, issue, source_dir, sandbox_dir, test_cmd, max_fix_rounds):
    try:
        workspace = Workspace(source_dir, sandbox_dir)
        _push(run_id, files_before=_snapshot(workspace))

        company = Company(llm=llm, max_workers=3, verbose=False)

        def on_progress(round_no, graph, scheduler):
            with _lock:
                RUNS[run_id]["round"] = round_no
                RUNS[run_id]["tasks"] = _serialize_graph(graph)
                RUNS[run_id]["log"] = list(scheduler.timeline)

        result = company.solve_repo_issue(
            workspace, issue, test_cmd,
            max_fix_rounds=max_fix_rounds, on_progress=on_progress,
        )

        with _lock:
            RUNS[run_id]["status"] = "finished"
            RUNS[run_id]["resolved"] = result["resolved"]
            RUNS[run_id]["rounds"] = result["rounds"]
            RUNS[run_id]["test_output"] = result.get("test_output", "")
            RUNS[run_id]["log"] = result["log"] + RUNS[run_id]["log"]
            RUNS[run_id]["files_after"] = _snapshot(workspace)
    except Exception:
        _fail(run_id)

def _snapshot(workspace: Workspace) -> dict:
    return {path: vf.content for path, vf in workspace.file_store.files.items()}

@app.route("/api/download/<run_id>")
def download_result(run_id: str):
    with _lock:
        state = RUNS.get(run_id)
    if state is None or not state.get("sandbox_dir"):
        return jsonify({"error": "no result to download for this run"}), 404
    sandbox = Path(state["sandbox_dir"])
    if not sandbox.exists():
        return jsonify({"error": "sandbox no longer on disk"}), 404

    zip_path = sandbox.parent / "result.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", sandbox)
    return send_file(zip_path, as_attachment=True, download_name=f"solved_{run_id}.zip")

def _new_state(issue, provider, mode) -> dict:
    return {
        "status": "running", "issue": issue, "provider": provider, "mode": mode,
        "tasks": [], "log": [], "error": None, "repo_mode": False,
        "round": 0, "resolved": None, "rounds": None, "test_output": "",
        "files_before": {}, "files_after": {}, "sandbox_dir": None,
    }

def _push(run_id: str, **kwargs) -> None:
    with _lock:
        if run_id in RUNS:
            RUNS[run_id].update(kwargs)

def _fail(run_id: str) -> None:
    with _lock:
        if run_id in RUNS:
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["error"] = traceback.format_exc(limit=4)

def _attach_live_polling(run_id, graph, scheduler) -> None:
    original_log = scheduler._log

    def patched_log(message: str) -> None:
        original_log(message)
        with _lock:
            if run_id in RUNS:
                RUNS[run_id]["tasks"] = _serialize_graph(graph)
                RUNS[run_id]["log"] = list(scheduler.timeline)

    scheduler._log = patched_log

def _serialize_graph(graph) -> list[dict]:
    return [{
        "id": t.task_id, "title": t.title, "role": t.role,
        "status": t.status.value, "depends_on": t.depends_on,
        "result": (t.result or "")[:1200], "attempts": t.attempts,
    } for t in graph.tasks.values()]

def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"unsafe path in zip: {member}")
        zf.extractall(dest)

@app.route("/api/status/<run_id>")
def status(run_id: str):
    with _lock:
        state = RUNS.get(run_id)
    if state is None:
        return jsonify({"error": "unknown run_id"}), 404
    return jsonify(state)

if __name__ == "__main__":
    print("AI Software Company Simulator UI -> http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
