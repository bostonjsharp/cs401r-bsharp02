"""
NorthStar Retail - Fairness / slice tests
CS 401R Lab 4, Task 1 (our addition to the starter kit)

Runs in the CodeBuild BUILD phase next to tests/test_model.py, against the
evaluation_metrics.json the training job emitted (EVAL_METRICS_PATH, or
--eval-metrics-path, see tests/conftest.py).

Design: FLAG, DO NOT BLOCK. A recall gap between loyalty tiers is a finding
the human approver must see before promoting the model, not a reason to
auto-reject it - the business may accept a model that under-serves Gold if
Gold churn is cheaper to recover by other means. So the gap test emits a
`warnings.warn("FAIRNESS FLAG: ...")`, which the buildspec's `-rw` puts in the
warnings summary of the build log, and still passes. What DOES fail the build
is a metrics file that lost the slices: a model whose per-tier behaviour cannot
be reviewed is not reviewable, and silently approving it is the real risk.

No SageMaker Clarify: it would need a processing job (money and minutes) to
compute what train() already computes in-process. The slice metrics are the
`tier_<tier>_*` keys written by evaluate_slices() in models/churn/churn_model.py.
Same principle as test_data.py: a missing metrics file is a FAILURE, not a skip.
"""

import json
import os
import warnings
from pathlib import Path

import pytest

TIERS = ["bronze", "silver", "gold", "platinum"]

# A recall gap larger than this between the best- and worst-served tier is
# flagged. 10 pp matches the `flagged` rule in evaluate_slices().
MAX_RECALL_GAP = 0.10

# Below these a slice cannot support a claim (same constants as evaluate_slices).
SLICE_MIN_N = 100
SLICE_MIN_POSITIVES = 10


@pytest.fixture(scope="module")
def eval_metrics():
    path = os.environ.get("EVAL_METRICS_PATH", "/tmp/eval_metrics.json")
    if not Path(path).exists():
        pytest.fail(
            f"Evaluation metrics not found at {path}. The fairness tests need the "
            f"evaluation_metrics.json the training job emitted; run the local "
            f"training check from the handout or pass --eval-metrics-path.",
            pytrace=False,
        )
    with open(path) as f:
        return json.load(f)


def _slice(metrics, tier):
    """Return (n, positives, auc, recall) for a tier; None where absent."""
    return {
        "n": metrics.get(f"tier_{tier}_n"),
        "positives": metrics.get(f"tier_{tier}_positives"),
        "auc": metrics.get(f"tier_{tier}_auc"),
        "recall": metrics.get(f"tier_{tier}_recall"),
    }


def _is_number(v):
    return isinstance(v, (int, float)) and v == v  # excludes None and NaN


class TestSliceMetricsPresent:

    def test_all_tiers_reported(self, eval_metrics):
        """Every loyalty tier must appear with n > 0 and a numeric recall.

        A model whose metrics lost the slices cannot be reviewed for tier bias,
        so this one DOES fail the build."""
        problems = []
        for tier in TIERS:
            s = _slice(eval_metrics, tier)
            if not _is_number(s["n"]) or s["n"] <= 0:
                problems.append(f"{tier}: n={s['n']!r}")
            elif not _is_number(s["recall"]):
                problems.append(f"{tier}: recall={s['recall']!r}")
        assert not problems, (
            f"Slice metrics missing or empty for: {problems}. train() must emit "
            f"tier_<tier>_n / _positives / _auc / _recall for Bronze, Silver, Gold, Platinum."
        )

    def test_slice_recalls_are_probabilities(self, eval_metrics):
        for tier in TIERS:
            r = _slice(eval_metrics, tier)["recall"]
            assert 0.0 <= r <= 1.0, f"tier_{tier}_recall={r} is not in [0, 1]"


class TestSliceFairness:

    def test_recall_gap_between_tiers_is_flagged_not_blocked(self, eval_metrics):
        """Flag a recall gap > 10 pp between the best- and worst-served tier.

        Lab 3 measured Bronze 0.45 vs Gold 0.12 at the same top-10% cutoff, so
        this is expected to fire. It warns (visible in the -rw summary of the
        build log) and still passes: promotion is the approver's call."""
        recalls = {}
        for tier in TIERS:
            s = _slice(eval_metrics, tier)
            if _is_number(s["recall"]) and not (
                s["n"] < SLICE_MIN_N or (s["positives"] or 0) < SLICE_MIN_POSITIVES
            ):
                recalls[tier] = s["recall"]
        assert len(recalls) >= 2, (
            f"Fewer than two tiers are large enough to compare: {recalls}"
        )
        best = max(recalls, key=recalls.get)
        worst = min(recalls, key=recalls.get)
        gap = recalls[best] - recalls[worst]
        if gap > MAX_RECALL_GAP:
            warnings.warn(
                f"FAIRNESS FLAG: recall gap {gap:.3f} between {best} "
                f"({recalls[best]:.3f}) and {worst} ({recalls[worst]:.3f}) exceeds "
                f"{MAX_RECALL_GAP:.2f} at the shared top-10% cutoff; review before "
                f"approving (flag, not block). All tiers: "
                + ", ".join(f"{t}={r:.3f}" for t, r in recalls.items())
            )
        # Deliberately no assertion on the gap: this test flags, it does not block.

    def test_small_slices_are_reported_not_judged(self, eval_metrics):
        """Tiers with n < 100 or positives < 10 cannot support a fairness claim.

        They are listed in a warning so the approver knows which tiers the gap
        test ignored; the gap test itself excludes them (mirrors evaluate_slices)."""
        small = []
        for tier in TIERS:
            s = _slice(eval_metrics, tier)
            if _is_number(s["n"]) and (
                s["n"] < SLICE_MIN_N or (s["positives"] or 0) < SLICE_MIN_POSITIVES
            ):
                small.append(f"{tier} (n={s['n']}, positives={s['positives']})")
        if small:
            warnings.warn(
                "FAIRNESS NOTE: slices too small to judge, excluded from the gap "
                "test: " + "; ".join(small)
            )
        # Always passes; a small slice is information, not a defect.
