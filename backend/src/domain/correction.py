"""Singular module-name compatibility re-export for correction workflows."""

from .corrections import *  # noqa: F403
from .corrections import (  # noqa: F401
    CorrectionLedger,
    apply_correction_batch,
    apply_corrections,
    merge_corrections,
    reconcile_corrections,
)
