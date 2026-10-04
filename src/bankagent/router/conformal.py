"""Split-conformal prediction sets for the learned router (Task 18).

Nonconformity score of a calibration message: ``1 - p(true intent)``. With ``n`` calibration
scores, ``q_hat`` is the ``ceil((n + 1)(1 - alpha))``-th smallest; an intent enters the set when
``1 - p <= q_hat``. If that rank exceeds ``n`` there are too few calibration messages for the
guarantee and ``q_hat = 1.0``, which puts every intent in the set (the router always abstains).

When calibration and new messages are exchangeable, the set contains the true intent with
probability at least ``1 - alpha``. The guarantee is marginal: it holds on average over
messages, not for each intent or dialect separately.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

# (n + 1)(1 - alpha) is computed in floating point: 160 * 0.9 gives 144.00000000000003, and a
# plain ceil would take the 145th score instead of the 144th.
_RANK_TOLERANCE = 1e-9


def conformal_rank(n: int, alpha: float) -> int:
    """The order statistic ``ceil((n + 1)(1 - alpha))`` that becomes ``q_hat``."""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if n < 1:
        raise ValueError("at least one calibration message is needed")
    return math.ceil((n + 1) * (1.0 - alpha) - _RANK_TOLERANCE)


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


def prediction_set(probs: NDArray[np.float64], q_hat: float) -> tuple[int, ...]:
    """Class indices with ``1 - p <= q_hat``, most probable first. May be empty."""
    order = np.argsort(-probs, kind="stable")
    return tuple(int(k) for k in order if 1.0 - probs[k] <= q_hat)
