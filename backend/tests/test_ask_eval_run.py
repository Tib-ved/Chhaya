"""The Ask Chhaya eval runner's pure parts: batches, rows, step split, the refusal stop. Never calls a model."""
import json

from evals.ask_chhaya import run


def _case(eval_id, status, score, seconds, question):
    return {
        "eval_id": eval_id, "final_eval_status": status,
        "overall_eval_metric_results": [
            {"metric_name": "response_match_score", "score": score, "eval_status": status},
            {"metric_name": "token_usage_v1", "score": 100, "eval_status": 1},
        ],
        "eval_metric_result_per_invocation": [{
            "actual_invocation": {"user_content": {"parts": [{"text": question}]},
                                  "final_response": {"parts": [{"text": "an answer"}]}},
            "eval_metric_results": [
                {"metric_name": "invocation_duration_v1", "score": seconds, "eval_status": 1},
                {"metric_name": "response_match_score", "score": score, "eval_status": status},
            ],
        }],
    }


def test_batches_are_consecutive_slices():
    ids = [f"q{i}" for i in range(15)]
    out = run.batches(ids, 5)
    assert out == [ids[0:5], ids[5:10], ids[10:15]]
    assert run.batches(ids, 0) == [ids]
    assert run.batches([], 5) == []


def test_refused_on_policy_spots_the_two_markers():
    assert run.refused_on_policy("x 403 PERMISSION_DENIED y")
    assert run.refused_on_policy("Request rejected in accordance with our terms of use")
    assert not run.refused_on_policy("all 5 cases passed")


def test_split_by_step_takes_the_median_per_invocation():
    text = "\n".join([
        "ask timing step=gate name=topic seconds=1.000 invocation=a",
        "ask timing step=model name=m seconds=2.000 invocation=a",
        "ask timing step=model name=m seconds=1.000 invocation=a",
        "ask timing step=tool name=t seconds=4.000 invocation=a",
        "ask timing step=gate name=topic seconds=3.000 invocation=b",
        "ask timing step=model name=m seconds=5.000 invocation=b",
        "ask timing step=tool name=t seconds=2.000 invocation=b",
    ])
    out = run.split_by_step(text)
    assert out == {"gate": 2.0, "model": 4.0, "tool": 3.0, "specialists": 0.0, "invocations": 2}
    assert run.split_by_step("") == {}


def test_split_by_step_skips_gate_lines_with_no_invocation():
    text = "ask timing step=gate name=topic seconds=9.000 invocation=\nask timing step=model name=chhaya seconds=2.000 invocation=a"
    assert run.split_by_step(text) == {"gate": 0.0, "model": 2.0, "tool": 0.0, "specialists": 0.0, "invocations": 1}


def test_split_by_step_counts_a_specialists_nested_run_in_its_answer_not_as_one():
    text = "\n".join([
        "ask timing step=gate name=topic seconds=1.000 invocation=root",
        "ask timing step=model name=chhaya seconds=2.000 invocation=root",
        # the analyst's nested run: its own invocation id, inside the root's call of it
        "ask timing step=model name=analyst seconds=3.000 invocation=nested",
        "ask timing step=tool name=count_cases seconds=0.500 invocation=nested",
        "ask timing step=model name=analyst seconds=1.000 invocation=nested",
        "ask timing step=tool name=analyst seconds=4.600 invocation=root",
        "ask timing step=tool name=get_round seconds=0.200 invocation=root",
        "ask timing step=model name=chhaya seconds=1.000 invocation=root",
    ])
    out = run.split_by_step(text)
    assert out == {"gate": 1.0, "model": 3.0, "tool": 0.2, "specialists": 4.6, "invocations": 1}


def test_summarise_gives_a_row_per_case():
    s = run.summarise("golden", [_case("a", 1, 0.9, 3.2, "How many red?"), _case("b", 2, 0.1, 7.0, "Who?")])
    assert [r["case"] for r in s["rows"]] == ["a", "b"]
    assert s["rows"][0] == {"run": 1, "case": "a", "question": "How many red?", "passed": True, "seconds": 3.2,
                            "checks": {"response_match_score": {"score": 0.9, "passed": True}}}
    assert s["rows"][1]["passed"] is False
    assert s["latency_s"]["max"] == 7.0


def test_main_refuses_adversarial_without_the_flag(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "x")
    monkeypatch.setattr(run, "run_batch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert run.main(["--set", "adversarial"]) == 2
    assert run.main(["--set", "all"]) == 2


def test_main_stops_after_a_policy_refusal(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GCP_PROJECT", "x")
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.setattr(run, "case_ids", lambda name: ["a", "b", "c", "d"])
    calls = []

    def fake(name, ids, env, log, config=None, agent=run.APP_AGENT):
        calls.append(ids)
        return [_case(ids[0], 1, 1.0, 1.0, "q")], "403 PERMISSION_DENIED", 1
    monkeypatch.setattr(run, "run_batch", fake)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    code = run.main(["--set", "golden", "--batch-size", "2"])
    assert code == 3
    assert calls == [["a", "b"]]
    assert "STOPPED: Vertex refused a request on policy grounds" in capsys.readouterr().out
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["rows"] and report["model"]
    assert report["expected"] == 4 and report["complete"] is False


def _main_with(monkeypatch, tmp_path, fake, argv):
    monkeypatch.setenv("GCP_PROJECT", "x")
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.setattr(run, "case_ids", lambda name: ["a", "b", "c", "d"])
    monkeypatch.setattr(run, "run_batch", fake)
    sleeps = []
    monkeypatch.setattr(run.time, "sleep", lambda s: sleeps.append(s))
    return run.main(argv), sleeps


def test_a_short_batch_stops_with_exit_4(monkeypatch, tmp_path, capsys):
    calls = []

    def fake(name, ids, env, log, config=None, agent=run.APP_AGENT):
        calls.append(ids)
        return [_case("a", 1, 1.0, 1.0, "q")], "fine", 0
    code, sleeps = _main_with(monkeypatch, tmp_path, fake, ["--batch-size", "2"])
    assert code == 4 and calls == [["a", "b"]] and sleeps == []
    assert "STOPPED: batch 1 returned no result for b" in capsys.readouterr().out
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["expected"] == 4 and report["cases"] == 1 and report["complete"] is False


def test_a_crashed_batch_with_no_results_stops_and_names_the_exit_code(monkeypatch, tmp_path, capsys):
    code, _ = _main_with(monkeypatch, tmp_path, lambda *a: ([], "Traceback", 1), [])
    assert code == 4
    assert "batch 1 returned no result for a, b, c, d (adk eval exited 1)" in capsys.readouterr().out


def test_a_complete_run_marks_each_row_with_its_run(monkeypatch, tmp_path):
    fake = lambda name, ids, env, log, config=None, agent=run.APP_AGENT: ([_case(i, 1, 1.0, 1.0, "q") for i in ids], "ok", 0)
    code, sleeps = _main_with(monkeypatch, tmp_path, fake, ["--runs", "2", "--pause-seconds", "7"])
    assert code == 0 and sleeps == [7]
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["complete"] is True and report["expected"] == 8
    assert [r["run"] for r in report["rows"]] == [1, 1, 1, 1, 2, 2, 2, 2]


def _report_for(monkeypatch, tmp_path, argv=()):
    agents = []

    def fake(name, ids, env, log, config=None, agent=run.APP_AGENT):
        agents.append(agent)
        return [_case(i, 1, 1.0, 1.0, "q") for i in ids], "ok", 0
    code, _ = _main_with(monkeypatch, tmp_path, fake, list(argv))
    assert code == 0
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    return report, next(tmp_path.glob("*.md")).read_text(encoding="utf-8"), agents


def test_a_claude_stand_in_run_says_so_in_the_report(monkeypatch, tmp_path):
    monkeypatch.delenv("ASK_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("ASK_CLAUDE_SMALL", raising=False)
    report, text, agents = _report_for(monkeypatch, tmp_path, ["--claude"])
    assert set(agents) == {run.CLAUDE_AGENT}
    assert report["model"] == "claude-opus-5-5" and report["stand_in"] is True
    assert report["small_model"] == "claude-haiku-5-5" and report["judge_model"] == "gemini-3.8-flash"
    assert "Claude (claude-opus-5-5) standing in for Gemini" in text and "graded by gemini-3.8-flash" in text


def test_a_stand_in_run_uses_the_models_named_in_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ASK_CLAUDE_MODEL", "claude-x")
    monkeypatch.setenv("ASK_CLAUDE_SMALL", "claude-y")
    report, _, _ = _report_for(monkeypatch, tmp_path, ["--claude"])
    assert report["model"] == "claude-x" and report["small_model"] == "claude-y"


def test_the_claude_agent_and_its_results_live_in_the_eval_harness():
    assert run.CLAUDE_AGENT.startswith("evals/") and (run.BACKEND / run.CLAUDE_AGENT / "agent.py").is_file()
    assert run.history(run.APP_AGENT) == run.BACKEND / "app" / "ask" / ".adk" / "eval_history"
    assert run.history(run.CLAUDE_AGENT) == run.BACKEND / run.CLAUDE_AGENT / ".adk" / "eval_history"


def test_a_gemini_run_is_not_a_stand_in(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_MODEL", "gemini-test")
    monkeypatch.setenv("ASK_CLAUDE_MODEL", "claude-x")  # ignored without --claude
    report, text, agents = _report_for(monkeypatch, tmp_path)
    assert set(agents) == {run.APP_AGENT}
    assert report["model"] == "gemini-test" and report["stand_in"] is False
    assert "small_model" not in report and report["judge_model"] == "gemini-3.8-flash"
    assert "standing in" not in text and "graded by gemini-3.8-flash" in text


def test_a_report_with_no_judge_recorded_says_so():
    for stand_in in (True, False):
        text = run.markdown([], "s", "c", {"model": "m", "stand_in": stand_in, "judge_model": None})
        assert "graded by an unrecorded judge." in text and "None" not in text


def test_a_run_starts_its_timing_file_afresh(monkeypatch, tmp_path):
    fake = lambda name, ids, env, log, config=None, agent=run.APP_AGENT: ([_case(i, 1, 1.0, 1.0, "q") for i in ids], "ok", 0)

    class Fixed(run.datetime):
        @classmethod
        def now(cls, tz=None):
            return run.datetime(2026, 10, 18, 9, 30)

    monkeypatch.setattr(run, "datetime", Fixed)
    stale = "ask timing step=model name=chhaya seconds=99.000 invocation=old\n"
    (tmp_path / "2026-10-18_0930.timing.log").write_text(stale, encoding="utf-8")
    code, _ = _main_with(monkeypatch, tmp_path, fake, [])
    assert code == 0
    report = json.loads((tmp_path / "2026-10-18_0930.json").read_text(encoding="utf-8"))
    assert report["steps_s"] == {}


def test_judge_model_grades_with_a_copy_of_the_config_and_the_report_names_it(monkeypatch, tmp_path):
    seen = []

    def fake(name, ids, env, log, config=None, agent=run.APP_AGENT):
        seen.append(config)
        return [_case(i, 1, 1.0, 1.0, "q") for i in ids], "ok", 0
    original = (run.HERE / "golden.test_config.json").read_text(encoding="utf-8")
    code, _ = _main_with(monkeypatch, tmp_path, fake, ["--judge-model", "claude-opus-5-5"])
    assert code == 0
    config = json.loads(seen[0].read_text(encoding="utf-8"))
    judges = [c["judge_model_options"]["judge_model"] for c in config["criteria"].values()
              if isinstance(c, dict) and "judge_model_options" in c]
    assert judges == ["claude-opus-5-5", "claude-opus-5-5"]
    assert config["custom_metrics"] == json.loads(original)["custom_metrics"]
    assert seen[0].parent != run.HERE and (run.HERE / "golden.test_config.json").read_text(encoding="utf-8") == original
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["judge_model"] == "claude-opus-5-5"
    assert "graded by claude-opus-5-5" in next(tmp_path.glob("*.md")).read_text(encoding="utf-8")


def test_without_judge_model_the_sets_own_config_is_used(monkeypatch, tmp_path):
    seen = []

    def fake(name, ids, env, log, config=None, agent=run.APP_AGENT):
        seen.append(config)
        return [_case(i, 1, 1.0, 1.0, "q") for i in ids], "ok", 0
    _main_with(monkeypatch, tmp_path, fake, [])
    assert seen == [None]
