from instapostsforge import cli
from instapostsforge.usage import UsageLog, estimate_cost, format_table, summarize


def usage(i, o):
    return {"input_tokens": i, "output_tokens": o}


def test_unknown_model_has_no_cost_estimate():
    assert estimate_cost("some-new-model", {"input_tokens": 10}, {}) is None


def test_summary_totals_per_agent(tmp_path):
    log = UsageLog("p", pricing={"m": {"input": 1, "output": 2}}, state_dir=tmp_path)
    log.record(agent="planner", model="m", tokens=usage(1_000_000, 0))
    log.record(agent="planner", model="m", tokens=usage(0, 1_000_000))
    log.record(agent="reviewer", model="m", tokens=usage(10, 10))
    rows = summarize(log.entries)
    assert rows["planner"]["calls"] == 2 and rows["planner"]["estimated_usd"] == 3.0
    assert rows["total"]["calls"] == 3
    table = format_table(rows)
    assert table[-1].startswith("| **total** | 3 |")


def test_forge_usage_command_reads_the_month(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    log = UsageLog("main", pricing={}, command="post")
    log.record(agent="caption_writer", model="claude-opus-5-5", tokens=usage(5, 5))
    month = log.entries[0]["at"][:7]
    assert cli.main(["usage", "-p", "main", "--month", month]) == 0
    out = capsys.readouterr().out
    assert "1 runs, 1 calls, 0 failed" in out and "caption_writer" in out


def test_audit_report_shows_each_phase_and_call(tmp_path):
    import json

    from instapostsforge.usage import write_report

    log = UsageLog("main", pricing={"m": {"input": 1, "output": 2}}, command="post", state_dir=tmp_path / "state")
    log.record(agent="analyst", model="m", tokens=usage(1000, 100))
    log.record(agent="caption_writer", model="m", tokens=usage(2000, 300))
    log.record(agent="caption_writer", model="m", ok=False, error="overloaded")
    log.record(agent="reviewer", model="m", tokens=usage(500, 50))
    path = write_report(log, tmp_path / "reports")
    text = path.read_text()
    assert "| Photo scanning | analyst | 1 |" in text
    assert "| Caption, quote and alt text | caption_writer | 2 (1 failed) |" in text
    assert "failed: overloaded" in text and "1 runs" in text
    data = json.loads(path.with_suffix(".json").read_text())
    assert [r["agent"] for r in data["phases"]] == ["analyst", "caption_writer", "reviewer"]
    assert data["phases"][1]["input_tokens"] == 2000


def test_no_report_without_ai_calls(tmp_path):
    from instapostsforge.usage import write_report

    assert write_report(UsageLog("main", state_dir=tmp_path), tmp_path / "reports") is None


def test_forge_reset_clears_a_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "state" / "main"
    (folder / "usage").mkdir(parents=True)
    (folder / "usage" / "2026-10.jsonl").write_text("{}\n")
    for name in ("posts.json", "insights.json", "catalog.json"):
        (folder / name).write_text('{"h:1": {}}')
    assert cli.main(["reset", "-p", "main", "--keep-catalog"]) == 0
    assert (folder / "posts.json").read_text() == "{}\n" and (folder / "insights.json").read_text() == "{}\n"
    assert (folder / "catalog.json").read_text() == '{"h:1": {}}' and not (folder / "usage").exists()
    assert "Photo analyses were kept" in capsys.readouterr().out
    assert cli.main(["reset", "-p", "main"]) == 0
    assert (folder / "catalog.json").read_text() == "{}\n"


def test_cache_prices_can_be_set_per_model():
    tokens = {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
    assert estimate_cost("m", tokens, {"m": {"input": 1, "output": 2}}) == 1 + 0.1 + 1.25
    assert estimate_cost("m", tokens, {"m": {"input": 1, "output": 2, "cache_read": 0.5, "cache_write": 0}}) == 1.5


def test_each_call_records_its_provider(tmp_path):
    log = UsageLog("p", state_dir=tmp_path)
    assert log.record(agent="planner", model="m", provider="openai")["provider"] == "openai"
