from datetime import date

import pytest
from pydantic import ValidationError

from workplace_platform.config import load_config


def test_demo_profile_window_matches_day_count(cfg):
    assert (cfg.end_date - cfg.start_date).days + 1 == cfg.profile.n_days


def test_every_region_is_represented_in_every_profile():
    """The CN/non-CN reconciliation is the point of the semantic layer, so a
    profile that happened to contain no CN sites would silently stop testing
    it."""
    for profile in ("demo", "full"):
        cfg = load_config(profile=profile)
        regions = {city.region for city in cfg.cities}
        assert regions == {"AMER", "EMEA", "APAC", "CN"}, profile


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown profile"):
        load_config(profile="does-not-exist")


def test_config_is_frozen(cfg):
    with pytest.raises(ValidationError):
        cfg.seed = 1


def test_end_date_is_a_real_date(cfg):
    assert isinstance(cfg.end_date, date)
