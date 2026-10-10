"""Search-call optimisation: fuzzy de-duplication and budget allocation."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz


def _same_merchant(a: str, b: str, threshold: int) -> bool:
    """Conservative typo test. A wrong merge gives a whole group the wrong enrichment,
    so we require: same word count, identical first word, and every word pair similar.
    "blue bottle"/"blue botle" -> merged; "shopa bakery"/"shopb bakery" -> kept apart;
    "uber"/"uber eats" -> kept apart.
    """
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb) or not ta or ta[0] != tb[0]:
        return False
    return all(x == y or fuzz.ratio(x, y) >= threshold for x, y in zip(ta, tb))


def cluster_keys(keys_by_count: dict[str, int], threshold: int = 90) -> dict[str, str]:
    """Map each key to a canonical key, merging near-identical spellings (typos, plurals).

    The most frequent spelling becomes the canonical one. threshold=100 disables merging.
    """
    ordered = sorted(keys_by_count, key=lambda k: (-keys_by_count[k], len(k), k))
    canonical: dict[str, str] = {}
    reps: list[str] = []
    for key in ordered:
        match = None
        if threshold < 100:
            for rep in reps:
                if _same_merchant(rep, key, threshold):
                    match = rep
                    break
        if match is None:
            reps.append(key)
            canonical[key] = key
        else:
            canonical[key] = match
    return canonical


@dataclass
class SearchCandidate:
    key: str
    confidence: float
    frequency: int  # number of input transactions sharing this merchant
    total_amount: float


def allocate_budget(
    candidates: list[SearchCandidate], budget: int
) -> tuple[list[SearchCandidate], list[SearchCandidate]]:
    """Spend the search budget where it helps most.

    Priority: merchants that affect the most transactions, then largest spend, then the
    least confident. Returns (to_search, skipped).
    """
    ranked = sorted(
        candidates,
        key=lambda c: (-c.frequency, -c.total_amount, c.confidence, c.key),
    )
    budget = max(0, budget)
    return ranked[:budget], ranked[budget:]
