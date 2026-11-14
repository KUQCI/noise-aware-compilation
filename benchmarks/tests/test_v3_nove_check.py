"""T3-C6: the development Nove check runs and records what it checked."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

from nac_bench import nove_check_dev


def test_cases_cover_the_development_devices() -> None:
    names = [name for _case, name in nove_check_dev.cases()]
    assert len(names) == len(set(names)) == 2 * (3 * 4 + 2 * 2)
    assert "miami-D7-zero" in names and "miami-restored-surface-d5-ZN-readout" in names


def test_check_case_small() -> None:
    by_name = dict((name, case) for case, name in nove_check_dev.cases())
    with ProcessPoolExecutor(max_workers=2) as pool:
        full = nove_check_dev.check_case(pool, by_name["fez-A-zero"], "fez-A-zero", limit=None)
        part = nove_check_dev.check_case(
            pool, by_name["miami-restored-surface-d5-ZZ-readout"], "x", limit=3
        )
    assert full["checked"] == full["placements"] == 738 and not full["sampled"]
    assert full["mismatches"] == []
    assert part["checked"] == 3 and part["placements"] == 64 and part["sampled"]
    assert part["mismatches"] == []
