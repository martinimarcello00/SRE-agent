# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_evaluation_judge.py
import evaluation.evaluation as ev
import utils.openai_usage as usage

# The guard must find the judge model under the dated name the usage API reports
fake_api = {
    "gpt-5-mini-2025-08-07": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2_000_000},
    "gpt-5.1-2025-11-13": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
}
usage.get_today_completions_usage = lambda **kw: fake_api
assert usage.get_today_model_usage(ev.GPT5_1_NAME)["total_tokens"] == 12
assert usage.get_today_model_usage("gpt-5-2025-08-07")["total_tokens"] == 0  # the old name matched nothing


class FakeJudge:
    calls = 0

    def with_structured_output(self, _):
        return self

    def invoke(self, prompt, config):
        FakeJudge.calls += 1
        return type("R", (), {"score": 4, "reasoning": "ok"})()


ev.GPT5_1 = FakeJudge()
scenario = {"RCA_gt": "gt"}

# Empty analyses never reach the judge or the usage API
ev.get_today_model_usage = lambda **kw: (_ for _ in ()).throw(AssertionError("usage API called"))
assert ev.evaluate_rca_analysis(scenario, "No analysis data available")[0] == 1
assert ev.evaluate_rca_analysis(scenario, "  ")[0] == 1 and FakeJudge.calls == 0

# Under the daily limit the judge runs, over it the score is None
ev.get_today_model_usage = lambda **kw: {"total_tokens": 100}
assert ev.evaluate_rca_analysis(scenario, "cart cannot reach valkey")[0] == 4 and FakeJudge.calls == 1
ev.get_today_model_usage = lambda **kw: {"total_tokens": ev.GPT5_1_TOKEN_DAILY_LIMIT + 1}
score, msg = ev.evaluate_rca_analysis(scenario, "cart cannot reach valkey")
assert score is None and "exceeded" in msg and FakeJudge.calls == 1
print("ok")
