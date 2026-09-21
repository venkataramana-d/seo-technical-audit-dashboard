"""Unit tests for near-duplicate content detection (02-AUDIT-ENGINE.md §2).

Pure, deterministic (exact Jaccard confirmation), no DB.
"""
from modules.near_duplicate import (
    content_signature,
    jaccard,
    near_duplicate_clusters,
    near_duplicate_content,
    shingle_ints,
)

# Representative page-length content: many distinct tokens (real pages are not a
# single repeated sentence - shingle SETS dedupe repetition, so short/repetitive
# fixtures aren't representative). NEAR differs from BASE by a couple of tokens.
_WORDS = [f"token{i}" for i in range(200)]
BASE = " ".join(_WORDS)
NEAR = " ".join("changed" if i in (50, 150) else w for i, w in enumerate(_WORDS))
DIFFERENT = " ".join(f"other{i}" for i in range(200))


def test_jaccard_and_shingles():
    a = shingle_ints("one two three four", k=2)
    b = shingle_ints("one two three four", k=2)
    assert jaccard(a, b) == 1.0
    assert jaccard(shingle_ints("a b c", k=2), shingle_ints("x y z", k=2)) == 0.0


def test_identical_content_is_near_duplicate():
    docs = {"http://s/a": BASE, "http://s/b": BASE}
    clusters = near_duplicate_clusters(docs)
    assert clusters == [["http://s/a", "http://s/b"]]


def test_lightly_edited_content_is_near_duplicate():
    # ~2 changed tokens in 200 words -> ~94% similar -> flagged at the 0.9 default.
    docs = {"http://s/a": BASE, "http://s/b": NEAR}
    clusters = near_duplicate_clusters(docs)
    assert len(clusters) == 1
    assert set(clusters[0]) == {"http://s/a", "http://s/b"}


def test_distinct_content_not_flagged():
    docs = {"http://s/a": BASE, "http://s/b": DIFFERENT}
    assert near_duplicate_clusters(docs) == []


def test_threshold_is_respected():
    # BASE vs NEAR are similar but not identical; a very high threshold rejects them.
    docs = {"http://s/a": BASE, "http://s/b": NEAR}
    assert near_duplicate_clusters(docs, threshold=0.99) == []


def test_cluster_of_three():
    docs = {"http://s/a": BASE, "http://s/b": BASE, "http://s/c": BASE, "http://s/d": DIFFERENT}
    clusters = near_duplicate_clusters(docs)
    assert len(clusters) == 1
    assert set(clusters[0]) == {"http://s/a", "http://s/b", "http://s/c"}


def test_empty_and_short_docs_skipped():
    docs = {"http://s/a": "", "http://s/b": "   ", "http://s/c": BASE}
    assert near_duplicate_clusters(docs) == []


def test_signature_is_deterministic():
    s1 = content_signature(BASE)
    s2 = content_signature(BASE)
    assert s1.minhash == s2.minhash
    assert s1.shingle_count > 0


def test_near_duplicate_content_emits_site_issue():
    docs = {"http://s/a": BASE, "http://s/b": BASE}
    issues = near_duplicate_content(docs)
    assert len(issues) == 1
    assert issues[0].issue_type == "near_duplicate_content"
    assert issues[0].category == "Content"
    assert set(issues[0].affected_urls) == {"http://s/a", "http://s/b"}
    assert issues[0].to_explanation_json()["affected_count"] == 2


def test_lsh_path_on_larger_input_still_finds_duplicates():
    # >50 docs triggers the MinHash+LSH candidate path; two clones must still cluster.
    docs = {f"http://s/{i}": DIFFERENT + f" item number {i} unique tail {i}" for i in range(60)}
    docs["http://s/clone1"] = BASE
    docs["http://s/clone2"] = BASE
    clusters = near_duplicate_clusters(docs)
    assert any(set(c) == {"http://s/clone1", "http://s/clone2"} for c in clusters)
