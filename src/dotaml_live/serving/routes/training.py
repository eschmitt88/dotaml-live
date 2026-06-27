"""Training-history endpoint — model progression for the dashboard 'Training' tab.

Read-only aggregation over the prequential health log + the per-run registry
metadata (on disk + recovered from git history). No model involved.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import training_history

router = APIRouter(prefix="/api", tags=["training"])


@router.get("/training-history")
def training_history_endpoint():
    """Prequential AUC series, per-run validation metrics for every version ever
    trained (beyond the keep_last_n kept on disk), and the promotion timeline."""
    return training_history.load_history()
