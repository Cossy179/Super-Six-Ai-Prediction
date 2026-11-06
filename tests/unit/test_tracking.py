from pathlib import Path

from super6.evaluation.tracking import experiment_record, log_experiment, read_log


def test_experiment_log_round_trip(tmp_path: Path) -> None:
    meta = {"label": "t", "start": "2020-01-01", "end": "2020-02-01", "competitions": ["E0"],
            "code_version": "x", "reference": "a", "models": ["a", "b"]}
    summary = {"overall": [{"model": "a", "fixtures": 10, "exact_log_loss": 2.9, "rps": 0.21,
                            "brier": 0.6, "points_points": 1.3, "jackpot_points": 1.1,
                            "jackpot_exact": 0.12, "ignored": 1}]}
    rec = experiment_record(meta, summary, {"markdown": tmp_path / "r.md"}, ["super6", "x"])
    assert rec["overall"][0] == {k: v for k, v in summary["overall"][0].items() if k != "ignored"}
    log_experiment(rec, tmp_path)
    log_experiment(rec, tmp_path)
    records = read_log(tmp_path)
    assert len(records) == 2 and records[0]["command"] == "super6 x"
    assert read_log(tmp_path / "missing") == []
