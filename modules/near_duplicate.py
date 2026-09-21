"""Near-duplicate content detection - 02-AUDIT-ENGINE.md §2 (Screaming Frog's
"near duplicates": shingling + similarity above a threshold, default ~90%).

Two-stage design, mirroring how SF (and the standard MinHash/LSH literature)
does it so it stays cheap on large crawls:

  1. Candidate generation - MinHash signatures + LSH banding bucket pages that
     are *probably* similar, avoiding an O(n^2) comparison of every page pair.
  2. Confirmation - exact Jaccard similarity on the shingle sets of only the
     candidate pairs decides what actually clears the threshold. Exact (not the
     MinHash estimate) so the result is deterministic and the threshold means
     exactly what it says.

Pure module - no DB, no network. The crawl-finalization glue supplies each
page's normalized content text (or a precomputed signature); results come back
as sitewide.SiteIssue clusters for the caller to persist.

Storage note for wiring: near-duplicate detection needs each page's content,
which the pages table does not currently store (only content_hash + word_count).
When wiring this in, either (a) persist normalized content text per page, or
(b) persist the compact MinHash signature (content_signature() below) per page
and switch confirmation to estimated Jaccard. (a) is exact; (b) scales better.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from modules.sitewide import SiteIssue

DEFAULT_THRESHOLD = 0.9  # SF's ~90% near-duplicate default
DEFAULT_SHINGLE_SIZE = 3  # words per shingle
DEFAULT_NUM_PERM = 128
_LSH_BANDS = 32
_LSH_ROWS = 4  # bands * rows must equal num_perm
_MERSENNE_PRIME = (1 << 61) - 1

# Deterministic MinHash coefficients - fixed seed so signatures are stable across
# runs/processes (a page's signature must be comparable to one computed earlier).
_rng = random.Random(0xC0FFEE)
_COEFFS = [
    (_rng.randrange(1, _MERSENNE_PRIME) | 1, _rng.randrange(0, _MERSENNE_PRIME))
    for _ in range(DEFAULT_NUM_PERM)
]


def _hash_shingle(shingle: str) -> int:
    return int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")


def shingle_ints(text: str, k: int = DEFAULT_SHINGLE_SIZE) -> set[int]:
    """Word k-shingles of `text`, hashed to ints. A document shorter than k words
    collapses to a single shingle of the whole thing."""
    words = text.split()
    if not words:
        return set()
    if len(words) <= k:
        return {_hash_shingle(" ".join(words))}
    return {_hash_shingle(" ".join(words[i:i + k])) for i in range(len(words) - k + 1)}


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass
class ContentSignature:
    """Compact per-page artifact suitable for storing in the DB and comparing
    later without keeping the full text. `shingle_count` lets a caller cheaply
    skip near-empty pages."""
    minhash: tuple[int, ...]
    shingle_count: int


def content_signature(text: str, k: int = DEFAULT_SHINGLE_SIZE,
                      num_perm: int = DEFAULT_NUM_PERM) -> ContentSignature:
    shingles = shingle_ints(text, k)
    return ContentSignature(minhash=_minhash(shingles, num_perm), shingle_count=len(shingles))


def _minhash(shingles: set[int], num_perm: int = DEFAULT_NUM_PERM) -> tuple[int, ...]:
    if not shingles:
        return tuple()
    coeffs = _COEFFS if num_perm == DEFAULT_NUM_PERM else _COEFFS[:num_perm]
    sig = []
    for a, b in coeffs:
        sig.append(min(((a * x + b) % _MERSENNE_PRIME) for x in shingles))
    return tuple(sig)


def estimated_jaccard(sig_a: tuple[int, ...], sig_b: tuple[int, ...]) -> float:
    """MinHash estimate of Jaccard - used only for LSH candidate scoring / the
    signature-only path, not for the exact confirmation step."""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


def _lsh_candidate_pairs(signatures: dict[str, tuple[int, ...]],
                         bands: int = _LSH_BANDS, rows: int = _LSH_ROWS) -> set[tuple[str, str]]:
    """Bucket signatures into bands; any two docs sharing a band bucket are a
    candidate near-duplicate pair. High recall for pairs above the LSH curve's
    midpoint, which for these params sits well below the 0.9 confirm threshold."""
    buckets: dict[tuple, list[str]] = {}
    for url, sig in signatures.items():
        if not sig:
            continue
        for band in range(bands):
            chunk = sig[band * rows:(band + 1) * rows]
            if len(chunk) < rows:
                break
            key = (band, chunk)
            buckets.setdefault(key, []).append(url)

    pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add(tuple(sorted((members[i], members[j]))))
    return pairs


def _union_find_clusters(pairs: set[tuple[str, str]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in pairs:
        union(a, b)

    clusters: dict[str, list[str]] = {}
    for node in parent:
        clusters.setdefault(find(node), []).append(node)
    return [sorted(members) for members in clusters.values() if len(members) >= 2]


def near_duplicate_clusters(
    documents: dict[str, str],
    threshold: float = DEFAULT_THRESHOLD,
    k: int = DEFAULT_SHINGLE_SIZE,
    num_perm: int = DEFAULT_NUM_PERM,
    use_lsh: bool = True,
) -> list[list[str]]:
    """Group URLs whose content is near-duplicate (exact Jaccard >= threshold).

    Uses MinHash+LSH to pick candidate pairs on large inputs; for small inputs
    (or use_lsh=False) it compares all pairs directly. The final decision is
    always exact Jaccard on the shingle sets, so it is deterministic.
    """
    shingles: dict[str, set[int]] = {}
    for url, text in documents.items():
        s = shingle_ints(text, k)
        if s:  # skip empty/near-empty content
            shingles[url] = s

    urls = list(shingles)
    if len(urls) < 2:
        return []

    if use_lsh and len(urls) > 50:
        sigs = {url: _minhash(shingles[url], num_perm) for url in urls}
        candidate_pairs = _lsh_candidate_pairs(sigs)
    else:
        candidate_pairs = {
            tuple(sorted((urls[i], urls[j])))
            for i in range(len(urls)) for j in range(i + 1, len(urls))
        }

    confirmed = {
        pair for pair in candidate_pairs
        if jaccard(shingles[pair[0]], shingles[pair[1]]) >= threshold
    }
    return _union_find_clusters(confirmed)


def _cluster_to_issue(cluster: list[str], threshold: float) -> SiteIssue:
    pct = round(threshold * 100)
    return SiteIssue(
        issue_type="near_duplicate_content", category="Content", severity="warning",
        impact_score=6, effort_level="medium",
        what=f"{len(cluster)} pages have near-duplicate content (>= {pct}% similar).",
        why=("Near-duplicate pages compete with each other in search results and dilute "
             "ranking signals, so search engines may index only one and ignore the rest."),
        root_cause=("Templated, boilerplate-heavy, or thin pages differ only slightly "
                    "(e.g. location/variant pages generated from one template)."),
        fix="Consolidate the pages, add substantial unique content, or canonicalize the variants to one URL.",
        affected_urls=cluster,
    )


def near_duplicate_content(
    documents: dict[str, str],
    threshold: float = DEFAULT_THRESHOLD,
    **kwargs,
) -> list[SiteIssue]:
    """Near-duplicate clusters as crawl-level SiteIssues - one issue per cluster
    of pages with >= threshold similar content (exact Jaccard on page text)."""
    return [
        _cluster_to_issue(cluster, threshold)
        for cluster in near_duplicate_clusters(documents, threshold=threshold, **kwargs)
    ]


def near_duplicate_from_signatures(
    signatures: dict[str, dict],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[SiteIssue]:
    """Near-duplicate detection from stored per-page MinHash signatures (the
    storage-friendly path used at crawl finalization - no page text needed).

    `signatures` maps url -> {"minhash": [...], "shingle_count": n} as persisted
    in pages.content_signature_json. Uses LSH for candidate generation on large
    inputs and the MinHash *estimate* of Jaccard for the threshold (matching SF's
    approach; exact text isn't available here).
    """
    sigs = {
        url: tuple(s["minhash"])
        for url, s in signatures.items()
        if s and s.get("minhash")
    }
    urls = list(sigs)
    if len(urls) < 2:
        return []

    if len(urls) > 50:
        candidate_pairs = _lsh_candidate_pairs(sigs)
    else:
        candidate_pairs = {
            tuple(sorted((urls[i], urls[j])))
            for i in range(len(urls)) for j in range(i + 1, len(urls))
        }

    confirmed = {
        pair for pair in candidate_pairs
        if estimated_jaccard(sigs[pair[0]], sigs[pair[1]]) >= threshold
    }
    return [_cluster_to_issue(cluster, threshold) for cluster in _union_find_clusters(confirmed)]
