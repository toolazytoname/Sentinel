"""Public API for S2 momentum rotation."""
from .strategy import (
    RotationParams,
    compute_universe,
    equal_weights,
    rotation_plan,
    select_top_k,
    top_k_mask,
    weekly_rotation_mask,
)

__all__ = [
    "RotationParams",
    "compute_universe",
    "equal_weights",
    "rotation_plan",
    "select_top_k",
    "top_k_mask",
    "weekly_rotation_mask",
]
