"""Lightweight experiment tracking (DESIGN.md §7.1: "MLflow or equivalent").

Every backtest or experiment run appends one JSON record to ``reports/experiments.jsonl``:

* when it ran and the exact command;
* the code version, test window, competitions and model configurations;
* headline metrics per model, and where the full outputs were written.

`super6 experiments` lists the log. Curated write-ups and promotion decisions live in
``docs/experiments/``, which is the record of what was decided and why.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_NAME = "experiments.jsonl"
HEADLINE = ("fixtures", "exact_log_loss", "rps", "brier", "points_points", "jackpot_points",
            "jackpot_exact")


def experiment_record(meta: Mapping[str, Any], summary: Mapping[str, Any],
                      outputs: Mapping[str, Path], command: list[str] | None = None
                      ) -> dict[str, Any]:
    now = datetime.now(UTC)
    overall = [{k: row[k] for k in ("model", *HEADLINE) if k in row}
               for row in summary.get("overall", [])]
    return {
        "id": f"{meta.get('label', 'run')}@{now:%Y%m%dT%H%M%SZ}",
        "logged_at": now.isoformat(),
        "label": meta.get("label"),
        "window": [meta.get("start"), meta.get("end")],
        "competitions": meta.get("competitions"),
        "code_version": meta.get("code_version"),
        "reference": meta.get("reference"),
        "models": meta.get("models"),
        "command": " ".join(command if command is not None else sys.argv),
        "overall": overall,
        "outputs": {k: str(v) for k, v in outputs.items()},
    }


def log_experiment(record: Mapping[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / LOG_NAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return path


def read_log(reports_dir: Path) -> list[dict[str, Any]]:
    path = reports_dir / LOG_NAME
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
