"""Seeded random streams.

Every generator draws from its own named substream. That means adding a source
system, or changing how one of them works, cannot shift the numbers produced by
any of the others - which is what makes "regenerate and diff" a useful habit.
"""

from __future__ import annotations

import hashlib

import numpy as np


def substream(seed: int, name: str) -> np.random.Generator:
    """A generator keyed by ``(seed, name)``, stable across runs and platforms."""
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    entropy = int.from_bytes(digest[:8], "big")
    return np.random.default_rng(entropy)


def lognormal_from_mean(
    rng: np.random.Generator,
    mean: float,
    sigma: float,
    size: int | tuple[int, ...],
) -> np.ndarray:
    """Lognormal draws whose *arithmetic* mean is ``mean``.

    numpy parameterises lognormal by the mean of the underlying normal, which is
    not the mean anyone actually has in mind when they write ``mean: 1100`` in a
    config file. This does the correction so the config reads the obvious way.
    """
    mu = np.log(mean) - 0.5 * sigma**2
    return rng.lognormal(mu, sigma, size)


def choice_from_mix(
    rng: np.random.Generator,
    mix: dict[str, float],
    size: int,
) -> np.ndarray:
    """Draw labels from a ``{label: probability}`` mapping."""
    labels = list(mix)
    weights = np.array([mix[k] for k in labels], dtype=float)
    weights = weights / weights.sum()
    return rng.choice(labels, size=size, p=weights)


def beta_around(
    rng: np.random.Generator,
    mean: float,
    concentration: float,
    size: int,
) -> np.ndarray:
    """Beta draws with the given mean and concentration (alpha + beta)."""
    mean = float(np.clip(mean, 1e-3, 1 - 1e-3))
    alpha = mean * concentration
    beta = (1.0 - mean) * concentration
    return rng.beta(alpha, beta, size)
