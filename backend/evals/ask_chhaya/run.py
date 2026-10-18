"""Run Ask Chhaya's evaluation with `adk eval` and write a report for the submission.

    pip install -r evals/requirements.txt
    python -m evals.ask_chhaya.run --batch-size 5      # the golden set in batches of 5, 180 s apart
    python -m evals.ask_chhaya.run --runs 3
    python -m evals.ask_chhaya.run --claude --judge-model claude-opus-5-5   # in backend/.venv-claude

The adversarial set only runs with --allow-adversarial. Needs Vertex AI credentials (gcloud auth
application-default login) and GCP_PROJECT. Reads the fixed dataset in fixture.py, never the live store.
Writes reports/<time>.json, .md, .log (the adk output) and .timing.log (the step timings).
"""
import argparse
import glob
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
BACKEND = HERE.parent.parent
APP_AGENT = "app/ask"  # Ask Chhaya as the service runs it, on Gemini
# Claude standing in for Gemini, for the evaluation only: the same agents, built in evals/ask_chhaya/claude_agent.
CLAUDE_AGENT = "evals/ask_chhaya/claude_agent"
REPORTS = HERE / "reports"
SETS = ("golden", "adversarial")
INFORMATIONAL = ("invocation_duration_v1", "token_usage_v1", "tool_call_count_v1", "inference_call_count_v1")
POLICY_REFUSALS = ("rejected in accordance with", "PERMISSION_DENIED")
TIMING_LINE = re.compile(r"ask timing step=(gate|model|tool) name=(\S*) seconds=([\d.]+) invocation=(\S*)")
ROOT_AGENT = "chhaya"
# The specialists run as tools of the root agent, each in a nested run with an invocation id of its own.
SPECIALISTS = ("analyst", "call_summarizer")


def judge_model() -> str | None:
    """The model that grades the answers, from the golden set's config."""
    config = json.loads((HERE / "golden.test_config.json").read_text(encoding="utf-8"))
    options = config.get("criteria", {}).get("final_response_match_v2", {}).get("judge_model_options") or {}
    return options.get("judge_model")


def with_judge(name: str, judge: str, folder: Path) -> Path:
    """A copy of a set's test config, in `folder`, with every judge model replaced by `judge`."""
    config = json.loads((HERE / f"{name}.test_config.json").read_text(encoding="utf-8"))
    for criterion in config.get("criteria", {}).values():
        if isinstance(criterion, dict) and isinstance(criterion.get("judge_model_options"), dict):
            criterion["judge_model_options"]["judge_model"] = judge
    path = folder / f"{name}.test_config.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def history(agent: str) -> Path:
    """Where adk eval writes its results: next to the agent it ran."""
    return BACKEND / agent / ".adk" / "eval_history"


def answering(env: dict, judge: str | None = None, claude: bool = False) -> dict:
    """Which model answered: Claude with --claude (a stand-in for Gemini), otherwise TEXT_MODEL.
    The judge is the one given with --judge-model, else the golden set's config's."""
    judge = judge or judge_model()
    if claude:
        return {"model": env.get("ASK_CLAUDE_MODEL") or "claude-opus-5-5", "stand_in": True,
                "small_model": env.get("ASK_CLAUDE_SMALL") or "claude-haiku-5-5", "judge_model": judge}
    return {"model": env["TEXT_MODEL"], "stand_in": False, "judge_model": judge}


def case_ids(name: str) -> list[str]:
    data = json.loads((HERE / f"{name}.evalset.json").read_text(encoding="utf-8"))
    return [case["eval_id"] for case in data["eval_cases"]]


def batches(ids: list[str], size: int) -> list[list[str]]:
    if not ids:
        return []
    size = size if size > 0 else len(ids)
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def refused_on_policy(output: str) -> bool:
    return any(marker in output for marker in POLICY_REFUSALS)


def run_batch(name: str, ids: list[str], env: dict, log: Path, config: Path | None = None,
              agent: str = APP_AGENT) -> tuple[list[dict], str, int]:
    """Run some cases of one set with adk eval; returns their results and the captured output.
    config is the test config to use instead of the set's own (one with another judge); agent is the
    agent folder adk eval runs (app/ask, or the Claude stand-in)."""
    started = time.time()
    adk = shutil.which("adk", path=str(Path(sys.executable).parent)) or "adk"
    command = [adk, "eval", agent, f"{HERE / f'{name}.evalset.json'}:{','.join(ids)}",
               "--config_file_path", str(config or HERE / f"{name}.test_config.json")]
    print(f"running {name}: {', '.join(ids)}…", flush=True)
    done = subprocess.run(command, cwd=BACKEND, env=env, check=False, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = done.stdout or ""
    print(output, flush=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {name}: {', '.join(ids)}\n{output}\n")
    results = []
    for path in sorted(glob.glob(str(history(agent) / "*.evalset_result.json"))):
        if os.path.getmtime(path) >= started:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            results.append(json.loads(data) if isinstance(data, str) else data)
    return [case for result in results for case in result["eval_case_results"]], output, done.returncode


def split_by_step(timing_text: str) -> dict:
    """Median seconds per step across the answers. Lines with no invocation id (a classifier error) are skipped.

    An answer is a root invocation: one with a topic check or a model call of the root agent. A specialist's
    nested run is not an answer; its whole time is in the root's call of it, counted as "specialists" (its own
    model calls included), so that "tool" is the data tools alone."""
    per: dict[str, dict[str, float]] = {}
    roots: set[str] = set()
    for step, name, seconds, invocation in TIMING_LINE.findall(timing_text):
        if not invocation:
            continue
        if step == "gate" or (step == "model" and name == ROOT_AGENT):
            roots.add(invocation)
        if step == "tool" and name in SPECIALISTS:
            step = "specialists"
        sums = per.setdefault(invocation, {})
        sums[step] = sums.get(step, 0.0) + float(seconds)
    answers = [sums for invocation, sums in per.items() if invocation in roots]
    if not answers:
        return {}
    out = {}
    for step in ("gate", "model", "tool", "specialists"):
        values = [v[step] for v in answers if step in v]
        out[step] = round(statistics.median(values), 3) if values else 0.0
    return {**out, "invocations": len(answers)}


def _text(content: dict | None) -> str:
    return "".join(p.get("text") or "" for p in (content or {}).get("parts") or [] if not p.get("thought"))


def _latency(durations: list[float]) -> dict:
    durations = sorted(durations)
    if not durations:
        return {}
    return {"p50": round(statistics.median(durations), 1),
            "p95": round(durations[min(len(durations) - 1, int(0.95 * len(durations)))], 1),
            "max": round(durations[-1], 1)}


def summarise(name: str, cases: list[dict], runs: list[int] | None = None) -> dict:
    metrics: dict[str, list[tuple[float | None, int]]] = {}
    durations: list[float] = []
    failures = []
    rows = []
    for index, case in enumerate(cases):
        seconds: list[float] = []
        checks = {}
        question = ""
        for metric in case["overall_eval_metric_results"]:
            if metric["metric_name"] in INFORMATIONAL:
                continue
            metrics.setdefault(metric["metric_name"], []).append((metric.get("score"), metric["eval_status"]))
            checks[metric["metric_name"]] = {"score": metric.get("score"), "passed": metric["eval_status"] == 1}
        for invocation in case["eval_metric_result_per_invocation"]:
            question = question or _text(invocation["actual_invocation"].get("user_content"))
            for metric in invocation["eval_metric_results"]:
                if metric["metric_name"] == "invocation_duration_v1" and metric.get("score") is not None:
                    durations.append(metric["score"])
                    seconds.append(metric["score"])
                elif metric["eval_status"] == 2 and metric["metric_name"] not in INFORMATIONAL:
                    failures.append({"case": case["eval_id"], "metric": metric["metric_name"], "score": metric.get("score"),
                                     "question": _text(invocation["actual_invocation"].get("user_content")),
                                     "answer": _text(invocation["actual_invocation"].get("final_response"))[:400]})
        rows.append({"run": runs[index] if runs else 1, "case": case["eval_id"], "question": question, "passed": case["final_eval_status"] == 1,
                     "seconds": round(sum(seconds), 1) if seconds else None, "checks": checks})
    passed = sum(1 for c in cases if c["final_eval_status"] == 1)
    table = {m: {"mean": round(statistics.mean(s for s, _ in v if s is not None), 3) if any(s is not None for s, _ in v) else None,
                 "pass_rate": round(sum(1 for _, st in v if st == 1) / len(v), 3)} for m, v in metrics.items()}
    return {"set": name, "cases": len(cases), "passed": passed, "metrics": table, "latency_s": _latency(durations),
            "failures": failures, "rows": rows}


def markdown(summaries: list[dict], stamp: str, commit: str, meta: dict) -> str:
    judge = meta.get("judge_model") or "an unrecorded judge"
    if meta["stand_in"]:
        who = (f"Run with Claude ({meta['model']}) standing in for Gemini, which was congested on Vertex AI that day. "
               f"The deployed Ask Chhaya runs on Gemini. Answers graded by {judge}.")
    else:
        who = f"Answered by {meta['model']} on Vertex AI, graded by {judge}."
    lines = [f"# Ask Chhaya evaluation, {stamp}", "", f"Commit `{commit}`. Fixed dataset (`evals/ask_chhaya/fixture.py`), "
             f"run with `adk eval`. {who}", ""]
    for s in summaries:
        lines += [f"## {s['set'].capitalize()}: {s['passed']} of {s['cases']} case runs passed", ""]
        if s["latency_s"]:
            lines += [f"Time per turn: median {s['latency_s']['p50']} s, 95th percentile {s['latency_s']['p95']} s.", ""]
        lines += ["| Metric | Mean score | Pass rate |", "|---|---|---|"]
        lines += [f"| {m} | {v['mean']} | {v['pass_rate']:.0%} |" for m, v in s["metrics"].items()]
        if s.get("rows"):
            lines += ["", "### Each question", "", "| Case | Question | Result | Seconds |", "|---|---|---|---|"]
            lines += [f"| {r['case']} | {r['question'][:100].replace('|', '/').replace(chr(10), ' ')} | "
                      f"{'passed' if r['passed'] else 'failed'} | {r['seconds']} |" for r in s["rows"]]
        if s["failures"]:
            lines += ["", "### Failures", "", "| Case | Metric | Score | Answer |", "|---|---|---|---|"]
            lines += [f"| {f['case']} | {f['metric']} | {f['score']} | {f['answer'][:160].replace('|', '/').replace(chr(10), ' ')} |"
                      for f in s["failures"]]
        lines.append("")
    return "\n".join(lines)


def _write(summaries: list[dict], stamp: str, commit: str, meta: dict, run_at: str, steps: dict,
           expected: int, complete: bool) -> str:
    rows = [row for s in summaries for row in s["rows"]]
    durations = [r["seconds"] for r in rows if r["seconds"] is not None]
    REPORTS.mkdir(exist_ok=True)
    report = {"commit": commit, **meta, "run_at": run_at, "expected": expected, "complete": complete,
              "cases": sum(s["cases"] for s in summaries),
              "passed": sum(s["passed"] for s in summaries), "rows": rows, "latency_s": _latency(durations),
              "steps_s": steps, "summaries": summaries}
    (REPORTS / f"{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    text = markdown(summaries, stamp, commit, meta)
    (REPORTS / f"{stamp}.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=(*SETS, "all"), default="golden")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=0, help="questions per batch; 0 runs the whole set at once")
    parser.add_argument("--pause-seconds", type=int, default=180, help="wait between batches")
    parser.add_argument("--allow-adversarial", action="store_true")
    parser.add_argument("--claude", action="store_true", help="answer with Claude standing in for Gemini "
                        "(evals/ask_chhaya/claude_agent; needs backend/.venv-claude and ANTHROPIC_API_KEY)")
    parser.add_argument("--judge-model", help="grade with this model instead of the config's (claude-* calls the "
                        "Anthropic API with --claude)")
    args = parser.parse_args(argv)
    if args.set != "golden" and not args.allow_adversarial:
        print("The adversarial set is not run by default: on 14 October a run of it was followed by a Vertex block. "
              "Pass --allow-adversarial to run it anyway.")
        return 2
    env = {**os.environ, "ASK_FIXTURE": "1", "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"}
    env.setdefault("TEXT_MODEL", "gemini-3.8-flash")
    if not env.get("GCP_PROJECT"):
        print("set GCP_PROJECT (and sign in with gcloud auth application-default login)")
        return 2
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    REPORTS.mkdir(exist_ok=True)
    log, timing = REPORTS / f"{stamp}.log", REPORTS / f"{stamp}.timing.log"
    env["ASK_TIMING_FILE"] = str(timing)
    timing.write_text("", encoding="utf-8")  # the timings of this run only, even if the stamp was used before
    chosen = SETS if args.set == "all" else (args.set,)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=BACKEND).stdout.strip()
    run_at = datetime.now().isoformat(timespec="seconds")
    results: dict[str, list[dict]] = {name: [] for name in chosen}
    run_of: dict[str, list[int]] = {name: [] for name in chosen}
    configs: dict[str, Path | None] = {name: None for name in chosen}
    if args.judge_model:
        folder = Path(tempfile.mkdtemp(prefix="ask-eval-config-"))  # a copy, never the checked-in config
        configs = {name: with_judge(name, args.judge_model, folder) for name in chosen}

    def finish(code: int, complete: bool) -> int:
        steps = split_by_step(timing.read_text(encoding="utf-8")) if timing.exists() else {}
        summaries = [summarise(name, results[name], run_of[name]) for name in chosen]
        print(_write(summaries, stamp, commit, answering(env, args.judge_model, args.claude), run_at, steps, expected, complete))
        return code or (0 if all(s["passed"] == s["cases"] for s in summaries) else 1)

    work = [(run, name, ids) for run in range(1, args.runs + 1) for name in chosen
            for ids in batches(case_ids(name), args.batch_size)]
    expected = sum(len(ids) for _, _, ids in work)
    for number, (run, name, ids) in enumerate(work, 1):
        if number > 1:
            print(f"pausing {args.pause_seconds} s before the next batch…", flush=True)
            time.sleep(args.pause_seconds)
        cases, output, code = run_batch(name, ids, env, log, configs[name], CLAUDE_AGENT if args.claude else APP_AGENT)
        results[name] += cases
        run_of[name] += [run] * len(cases)
        if refused_on_policy(output):
            print("STOPPED: Vertex refused a request on policy grounds")
            return finish(3, False)
        missing = [i for i in ids if i not in {c["eval_id"] for c in cases}]
        if missing:
            note = f" (adk eval exited {code})" if code else ""
            print(f"STOPPED: batch {number} returned no result for {', '.join(missing)}{note}")
            return finish(4, False)
    return finish(0, True)


if __name__ == "__main__":
    sys.exit(main())
