"""Dixon–Coles (1997) adjustment of the team-strength Poisson model (DESIGN.md §10.3).

The τ multipliers for the scores 0–0, 0–1, 1–0 and 1–1 are::

    τ(0,0) = 1 − λμρ    τ(0,1) = 1 + λρ    τ(1,0) = 1 + μρ    τ(1,1) = 1 − ρ

They re-weight low-scoring outcomes without changing total probability. ρ is estimated
jointly with team strengths under the same time-decay weights, within ``rho_bounds``.
"""

from __future__ import annotations

from typing import ClassVar

from super6.models.poisson import PoissonModel


class DixonColesModel(PoissonModel):
    name: ClassVar[str] = "dixon_coles"
    description: ClassVar[str] = (
        "Dixon–Coles: time-decayed team-strength Poisson with low-score dependence (rho)"
    )
    dixon_coles: ClassVar[bool] = True
