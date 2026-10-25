from types import SimpleNamespace

from instapostsforge import cli
from instapostsforge.usage import UsageLog, estimate_cost, format_table, summarize


def usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def test_unknown_model_has_no_cost_estimate():
    assert estimate_cost("some-new-model", {"input_tokens": 10}, {}) is None


def test_summary_totals_per_agent(tmp_path):
    log = UsageLog("p", pricing={"m": {"input": 1, "output": 2}}, state_dir=tmp_path)
    log.record(agent="planner", model="m", usage=usage(1_000_000, 0))
    log.record(agent="planner", model="m", usage=usage(0, 1_000_000))
    log.record(agent="reviewer", model="m", usage=usage(10, 10))
    rows = summarize(log.entries)
    assert rows["planner"]["calls"] == 2 and rows["planner"]["estimated_usd"] == 3.0
    assert rows["total"]["calls"] == 3
    table = format_table(rows)
    assert table[-1].startswith("| **total** | 3 |")


def test_forge_usage_command_reads_the_month(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    log = UsageLog("main", pricing={}, command="post")
    log.record(agent="caption_writer", model="claude-opus-5-5", usage=usage(5, 5))
    month = log.entries[0]["at"][:7]
    assert cli.main(["usage", "-p", "main", "--month", month]) == 0
    out = capsys.readouterr().out
    assert "1 runs, 1 calls, 0 failed" in out and "caption_writer" in out
