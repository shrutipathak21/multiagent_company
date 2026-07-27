from ai_company.task_graph import Task, TaskGraph, TaskStatus, CycleError
from ai_company.conflict import FileStore, WriteConflict, three_way_merge
from ai_company.company import Company
from ai_company.agents import Agent, MockLLM, LLMBackend

def test_cycle_detection():
    g = TaskGraph()
    g.add_task(Task("a", "A", "", "backend", depends_on=["b"]))
    g.add_task(Task("b", "B", "", "backend", depends_on=["a"]))
    try:
        g.validate()
        assert False, "should have raised CycleError"
    except CycleError:
        pass
    print("PASS cycle detection")

def test_failure_propagation():
    class AlwaysFailLLM(LLMBackend):
        def complete(self, s, u):
            raise RuntimeError("model exploded")

    company = Company(verbose=False)

    company.agents["tech_lead"] = Agent("tech_lead", AlwaysFailLLM())
    graph = company.solve_issue("any issue")

    assert graph.tasks["spec"].status == TaskStatus.DONE
    assert graph.tasks["plan"].status == TaskStatus.FAILED
    assert graph.tasks["plan"].attempts == 3
    for tid in ["impl_backend", "impl_frontend", "qa_review", "security_review", "release"]:
        assert graph.tasks[tid].status == TaskStatus.BLOCKED, tid
    print("PASS failure propagation + retries")

def test_clean_commit_and_conflict():
    fs = FileStore()

    content_v1 = "line1\nline2\nline3\n"
    v = fs.commit("app.py", content_v1, base_version=0, author="backend")
    assert v == 1

    c_a, v_a = fs.checkout("app.py")
    c_b, v_b = fs.checkout("app.py")

    fs.commit("app.py", "LINE1-EDITED\nline2\nline3\n", base_version=v_a, author="frontend")

    v = fs.commit("app.py", "line1\nline2\nLINE3-EDITED\n", base_version=v_b, author="security")
    merged, _ = fs.checkout("app.py")
    assert "LINE1-EDITED" in merged and "LINE3-EDITED" in merged
    print("PASS disjoint edits auto-merged:", repr(merged))

    try:
        fs.commit("app.py", "LINE1-DIFFERENT\nline2\nline3\n", base_version=1, author="qa")
        assert False, "should have raised WriteConflict"
    except WriteConflict as wc:
        assert "LINE1-EDITED" in wc.latest_content
    print("PASS overlapping edits raise WriteConflict with rebase info")

def test_merge_pure_insertions_same_spot_conflict():
    base = "a\nb\n"
    theirs = "a\nX\nb\n"
    ours = "a\nY\nb\n"
    assert three_way_merge(base, theirs, ours) is None
    print("PASS same-spot insertions correctly refuse to auto-merge")

test_cycle_detection()
test_failure_propagation()
test_clean_commit_and_conflict()
test_merge_pure_insertions_same_spot_conflict()
print("\nALL TESTS PASSED")
