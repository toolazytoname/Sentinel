"""Public API for S2 momentum rotation."""
from .strategy import RotationParams, rotation_plan, select_top_k, compute_universe, equal_weights

__all__ = ["RotationParams", "rotation_plan", "select_top_k", "compute_universe", "equal_weights"]