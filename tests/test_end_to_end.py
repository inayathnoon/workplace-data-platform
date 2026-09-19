"""One end-to-end smoke test.

Deliberately a single test: it runs the whole pipeline on a cut-down profile
and asserts the claims the README makes. If this passes, the repo does what it
says; if it fails, none of the narrower tests matter.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from workplace_platform.config import DEFECT_MANIFEST, GROUND_TRUTH, WAREHOUSE_PATH
from workplace_platform.dq.runner import defect_recall, run_checks
from workplace_platform.semantic.compile import compile_metric
from workplace_platform.semantic.registry import load_registry
from workplace_platform.semantic.validate import validate


@pytest.mark.skipif(not WAREHOUSE_PATH.exists(), reason="run `make pipeline` first")
def test_pipeline_recovers_what_the_simulator_planted(cfg):
    registry = load_registry()

    # 1. The registry agrees with the warehouse, in both directions.
    report = validate(cfg, registry)
    assert report.ok, report.render()

    # 2. Every metric compiles and runs.
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        for metric in registry:
            grain = "week" if "week" in metric.time_grain else metric.time_grain[0]
            compiled = compile_metric(registry, metric.name, ["region"], grain, cfg=cfg)
            assert not con.execute(compiled.sql).df().empty, metric.name

        # 3. The headline number is recovered to within a tenth of a point.
        compiled = compile_metric(registry, "attendance_rate", ["region"], None, cfg=cfg)
        recovered = con.execute(compiled.sql).df().set_index("region")["attendance_rate"]
    finally:
        con.close()

    planted = json.loads(GROUND_TRUTH.read_text())["planted"]["attendance_rate_by_region_realised"]
    for region, expected in planted.items():
        assert float(recovered.loc[region]) == pytest.approx(expected, abs=0.001)

    # 4. The quality suite finds every defect the generator says it planted,
    #    and nothing fails that nobody wrote down a reason for.
    card = run_checks(cfg)
    assert not card.unexpected_failures, card.render()
    rows = defect_recall(card)
    assert rows and all(r["caught"] for r in rows)
    assert DEFECT_MANIFEST.exists()
