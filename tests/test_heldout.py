"""The held-out set (fuzzer/heldout/) counted into the same scorecard as the fuzzer's own cases."""
from decimal import Decimal

from fastapi.testclient import TestClient

from api import main
from fuzzer import heldout
from fuzzer import run as fuzzer

N = 40  # fuzzer cases; small so the test is quick


def test_every_held_out_case_validates_and_becomes_a_row():
    rows, invalid = heldout.rows()
    assert invalid == []
    assert len(rows) == len(heldout.cases()) == 61
    assert {r["anomaly"] is None for r in rows if r["expected"] in ("release", "escalate")} == {True}
    assert all(r["anomaly"] for r in rows if r["expected"] == "hold")


def test_combined_totals_are_fuzzer_plus_held_out(monkeypatch):
    held, _ = heldout.rows()
    alone = fuzzer.score(held)
    combined = fuzzer.run(N)
    monkeypatch.setattr(heldout, "rows", lambda: ([], []))
    fuzz = fuzzer.run(N)
    for k in ("cases", "mutated", "clean", "wrongful_if_every_button_approved", "wrongful_releases", "escalated_mutated"):
        assert combined[k] == fuzz[k] + alone[k], k
    for k in ("dollars_protected", "dollars_delayed"):
        assert Decimal(combined[k]) == Decimal(fuzz[k]) + Decimal(alone[k]), k
    for kind, t in combined["by_anomaly"].items():
        for f in ("n", "caught", "wrongful", "right_rule"):
            assert t[f] == fuzz["by_anomaly"].get(kind, {}).get(f, 0) + alone["by_anomaly"].get(kind, {}).get(f, 0)
    stopped = lambda c: c["mutated"] - c["wrongful_if_every_button_approved"]
    assert combined["rule_catch_rate"] == round(stopped(combined) / combined["mutated"], 4)
    assert combined["heldout_invalid"] == []


def test_scorecard_endpoint_includes_held_out_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "SCORECARD", tmp_path / "scorecard.json")
    monkeypatch.setenv("SCORECARD_CASES", str(N))
    card = TestClient(main.app).get("/scorecard").json()
    assert card["cases"] == N + 61
    assert "billing_unapproved_co" in card["by_anomaly"]  # a held-out-only attack type
    assert any(m["case"].startswith("heldout:") for m in card["misses"] if isinstance(m["case"], str))
