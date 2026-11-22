"""
Laptop tests for the Lab 6 monitoring code. No AWS, no Evidently.

    .venv/Scripts/python.exe -m pytest monitoring/tests -q

evidently_drift.py imports Evidently at module level and Evidently is only
installed inside the processing container, so the import is stubbed here.
Only drift_direction(), DRIFT_PLAN and FEATURE_COLUMNS are exercised.
"""

import importlib
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MONITORING = os.path.dirname(HERE)
sys.path.insert(0, MONITORING)
sys.path.insert(0, os.path.dirname(MONITORING))


@pytest.fixture(scope="module")
def drift():
    stub = types.ModuleType("evidently")
    stub.Dataset = stub.DataDefinition = stub.Report = object
    metrics = types.ModuleType("evidently.metrics")
    metrics.ValueDrift = metrics.DriftedColumnsCount = object
    saved = {k: sys.modules.get(k) for k in ("evidently", "evidently.metrics")}
    sys.modules["evidently"], sys.modules["evidently.metrics"] = stub, metrics
    try:
        yield importlib.import_module("evidently_drift")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# The handout's synthetic table: mean shift -> (KS p-value, PSI statistic),
# thresholds ks 0.05 / psi 0.2.
SHIFTS = [
    (0, 0.272552, 0.0235),
    (2, 0.000000, 0.0472),
    (5, 0.000000, 0.2757),
    (15, 0.000000, 2.3176),
]


@pytest.mark.parametrize("shift,ks_p,psi", SHIFTS)
def test_ks_is_a_p_value(drift, shift, ks_p, psi):
    # KS falls with drift: only the unshifted row is clean.
    assert drift.drift_direction("ks", ks_p, 0.05) is (shift != 0)


@pytest.mark.parametrize("shift,ks_p,psi", SHIFTS)
def test_psi_is_a_statistic(drift, shift, ks_p, psi):
    # PSI rises with drift: it crosses 0.2 between shift 2 and shift 5.
    assert drift.drift_direction("psi", psi, 0.2) is (shift >= 5)


def test_the_trap_itself(drift):
    """One shared `value > threshold` calls maximal KS drift clean. Ours must not."""
    assert not (0.0 > 0.05)                       # the naive comparison
    assert drift.drift_direction("ks", 0.0, 0.05) is True


def test_boundaries_are_not_drift(drift):
    assert drift.drift_direction("psi", 0.2, 0.2) is False
    assert drift.drift_direction("ks", 0.05, 0.05) is False


def test_method_name_is_case_insensitive(drift):
    assert drift.drift_direction("PSI", 0.3, 0.2) is True
    assert drift.drift_direction("KS", 0.01, 0.05) is True


def test_unknown_method_raises(drift):
    with pytest.raises(ValueError):
        drift.drift_direction("made_up_test", 0.5, 0.1)


def test_nan_raises(drift):
    with pytest.raises(ValueError):
        drift.drift_direction("psi", float("nan"), 0.2)


def test_every_planned_method_is_handled(drift):
    for feature, (method, threshold) in drift.DRIFT_PLAN.items():
        assert feature in drift.FEATURE_COLUMNS
        drift.drift_direction(method, threshold, threshold)   # must not raise


def test_stub_is_gone():
    with open(os.path.join(MONITORING, "evidently_drift.py")) as fh:
        assert "raise NotImplementedError" not in fh.read()


def test_feature_columns_match_training(drift):
    """The container's copy of the feature list must equal the training code's."""
    from models.churn.churn_model import FEATURE_COLUMNS
    assert list(drift.FEATURE_COLUMNS) == list(FEATURE_COLUMNS)
    assert len(drift.FEATURE_COLUMNS) == 11


def test_publisher_maps_every_planned_method(drift):
    import publish_metrics
    for method, _ in drift.DRIFT_PLAN.values():
        assert method in publish_metrics.METRIC_FOR_METHOD


def test_publisher_datapoints(drift):
    import datetime as dt
    import publish_metrics
    summary = {
        "drift_detected": True, "violation_count": 1,
        "per_feature": {
            "avg_order_value": {"method": "psi", "threshold": 0.2, "value": 1.388, "drifted": True},
            "total_spend_90d": {"method": "ks", "threshold": 0.05, "value": 0.76, "drifted": False},
        },
    }
    now = dt.datetime(2026, 11, 24, tzinfo=dt.timezone.utc)
    data = publish_metrics.drift_metric_data(summary, "northstar-churn-prod", now)
    by_name = {d["MetricName"]: d for d in data}
    assert by_name["DriftDetected"]["Value"] == 1.0
    assert by_name["FeaturePSI"]["Value"] == 1.388
    assert by_name["FeatureKSPValue"]["Value"] == 0.76
    assert all(d["Dimensions"][0] == {"Name": "EndpointName", "Value": "northstar-churn-prod"}
               for d in data)

    biz = publish_metrics.business_metric_data({"churn_alerts": 70}, "ep", now, average=100.0)
    assert {d["MetricName"]: d["Value"] for d in biz} == {
        "ChurnAlertVolume": 70.0, "ChurnAlertVolumeRatio": 0.7}
    first_day = publish_metrics.business_metric_data({"churn_alerts": 70}, "ep", now, average=None)
    assert first_day[1]["Value"] == 1.0
