import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_portfolio_comparison.py"
SPEC = importlib.util.spec_from_file_location("build_portfolio_comparison", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_market_point_summary_uses_observed_range_instead_of_hardcoded_count() -> None:
    rows = [{"market_points": 1213}, {"market_points": 1214}, {"market_points": None}]

    assert MODULE.market_point_summary(rows) == "1,213–1,214 點"
