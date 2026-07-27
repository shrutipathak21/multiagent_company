from pathlib import Path
from ai_company.planner import parse_plan, PlanParseError
from ai_company.repo import Workspace, extract_file_blocks
from ai_company.company import Company
from ai_company.agents import LLMBackend

def test_plan_parsing_happy_path_with_fences_and_prose():
    raw = 'Sure! Here is the plan:\n```json\n[{"task_id":"a","title":"t","description":"d","role":"backend","depends_on":[]},{"task_id":"b","title":"t","description":"d","role":"qa","depends_on":["a"]}]\n```'
    g = parse_plan(raw)
    assert set(g.tasks) == {"a", "b"}
    print("PASS plan parsing (fences + prose repair)")

def test_plan_rejects_bad_role_unknown_dep_and_cycle():
    for bad, why in [
        ('[{"task_id":"a","role":"ceo","depends_on":[]}]', "unknown role"),
        ('[{"task_id":"a","role":"backend","depends_on":["ghost"]}]', "unknown dep"),
        ('[{"task_id":"a","role":"backend","depends_on":["b"]},'
         '{"task_id":"b","role":"qa","depends_on":["a"]}]', "cycle"),
    ]:
        try:
            parse_plan(bad)
            assert False, f"should reject: {why}"
        except PlanParseError:
            pass
    print("PASS plan validation (role / dep / cycle)")

def test_file_block_extraction_and_path_safety():
    out = "text\n```file:src/x.py\nprint(1)\n```\nmore\n```file:README.md\nhi\n```"
    files = extract_file_blocks(out)
    assert set(files) == {"src/x.py", "README.md"}
    ws = Workspace("sample_repo", "/tmp/ws_safety")
    try:
        ws.apply_agent_output("```file:../../etc/passwd\nx\n```", author="evil")
        assert False, "should reject path escape"
    except ValueError:
        pass
    print("PASS file blocks + path traversal rejected")

def test_fix_round_recovers_from_first_failure():
    class LearnsFromFailure(LLMBackend):
        def complete(self, sp, up):
            if "OUTPUT_JSON_PLAN" in up:
                return ('[{"task_id":"impl","title":"t","description":"d",'
                        '"role":"backend","depends_on":[]}]')
            if "backend engineer" in sp.lower():
                if "FAILED tests" in up:
                    return ('```file:mymath.py\ndef add(a,b):\n    return a+b\n'
                            'def divide(a,b):\n'
                            '    if b == 0:\n        raise ValueError("div0")\n'
                            '    return a/b\n'
                            'def power(b,e):\n    return b**e\n```')
                return ('```file:mymath.py\ndef add(a,b):\n    return a+b\n'
                        'def divide(a,b):\n    return a/b\n'
                        'def power(b,e):\n    return b**e\n```')
            return "ok"

    ws = Workspace("sample_repo", "/tmp/ws_fixround")
    company = Company(llm=LearnsFromFailure(), verbose=False)
    result = company.solve_repo_issue(
        ws, "Implement divide (ValueError on zero) and power.",
        test_cmd="python3 -m pytest -q")
    assert result["resolved"] is True
    assert result["rounds"] == 2, result["log"]
    print("PASS fix round: failed round 1, recovered in round 2")

test_plan_parsing_happy_path_with_fences_and_prose()
test_plan_rejects_bad_role_unknown_dep_and_cycle()
test_file_block_extraction_and_path_safety()
test_fix_round_recovers_from_first_failure()
print("\nALL V2 TESTS PASSED")
