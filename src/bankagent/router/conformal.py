"""Split-conformal prediction sets for the learned router (Task 18).

Nonconformity score of a calibration message: ``1 - p(true intent)``. For a scenario with five
dialect versions, calibration uses the maximum score in that scenario. With ``n`` calibration
scenarios, ``q_hat`` is the ``ceil((n + 1)(1 - alpha))``-th smallest scenario score. If that rank
exceeds ``n``, ``q_hat = 1.0`` and the router always abstains.

When calibration and new scenarios are exchangeable, all dialect versions of a new scenario
are covered together with probability at least ``1 - alpha``. The guarantee is marginal over
scenarios, not conditional on an intent or dialect.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_CEILING, Decimal

import numpy as np
from numpy.typing import NDArray


def conformal_rank(n: int, alpha: float) -> int:
    """The order statistic ``ceil((n + 1)(1 - alpha))`` that becomes ``q_hat``."""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if n < 1:
        raise ValueError("at least one calibration message is needed")
    # Treat the user's decimal alpha as written. A fixed float tolerance can lower a rank that
    # is genuinely just above an integer and invalidate the coverage guarantee.
    level = (n + 1) * (Decimal(1) - Decimal(str(alpha)))
    return int(level.to_integral_value(rounding=ROUND_CEILING))


def conformal_threshold(
    cal_probs: NDArray[np.float64], cal_labels: NDArray[np.int64], alpha: float
) -> float:
    """``q_hat`` from calibration probabilities (one row per message) and true class indices."""
    n = len(cal_labels)
    if cal_probs.ndim != 2 or cal_probs.shape[0] != n:
        raise ValueError("cal_probs must have one row per calibration label")
    rank = conformal_rank(n, alpha)
    if rank > n:
        return 1.0
    scores = 1.0 - cal_probs[np.arange(n), cal_labels]
    return float(np.sort(scores)[rank - 1])


def grouped_conformal_threshold(
    cal_probs: NDArray[np.float64],
    cal_labels: NDArray[np.int64],
    groups: Sequence[str],
    alpha: float,
) -> float:
    """Calibrate on scenario maxima so a new scenario's dialects are covered together."""
    n = len(cal_labels)
    if cal_probs.ndim != 2 or cal_probs.shape[0] != n or len(groups) != n:
        raise ValueError("probabilities, labels and groups must have the same row count")
    maxima: dict[str, float] = {}
    for score, group in zip(1.0 - cal_probs[np.arange(n), cal_labels], groups, strict=True):
        maxima[group] = max(maxima.get(group, 0.0), float(score))
    rank = conformal_rank(len(maxima), alpha)
    if rank > len(maxima):
        return 1.0
    return sorted(maxima.values())[rank - 1]


def prediction_set(probs: NDArray[np.float64], q_hat: float) -> tuple[int, ...]:
    """Class indices with ``1 - p <= q_hat``, most probable first. May be empty."""
    order = np.argsort(-probs, kind="stable")
    return tuple(int(k) for k in order if 1.0 - probs[k] <= q_hat)
