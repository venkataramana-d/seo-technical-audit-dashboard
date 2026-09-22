"""M3 Tier B #6: fuzzy near-duplicate detection from stored MinHash signatures."""

from modules.near_duplicate import content_signature, near_duplicate_from_signatures


def _sig(text):
    s = content_signature(text)
    return {"minhash": list(s.minhash), "shingle_count": s.shingle_count}


# A long, realistic paragraph; the variant changes only one single word, so the
# 3-shingle sets are >90% identical.
BASE = (
    "Corporate leadership training builds the skills managers need to guide teams "
    "through change, communicate a clear vision, and make sound decisions under "
    "pressure. Our instructor led workshops combine practical exercises, real world "
    "case studies, and personalized coaching so participants can apply what they "
    "learn immediately in their roles across the organization. The programme covers "
    "delegation, feedback, conflict resolution, strategic planning, stakeholder "
    "communication, and measuring team performance over time, with follow up "
    "sessions to reinforce new habits and sustain lasting behaviour change at work."
)
VARIANT = BASE.replace("sound decisions", "solid decisions")
DIFFERENT = (
    "Data engineering bootcamps teach the pipelines, warehousing, and streaming "
    "systems that power modern analytics, with hands on labs in Python, SQL, and "
    "distributed processing frameworks used by high growth technology companies."
)


def test_near_duplicate_clusters_from_signatures():
    signatures = {
        "https://x.com/a": _sig(BASE),
        "https://x.com/b": _sig(VARIANT),
        "https://x.com/c": _sig(DIFFERENT),
    }
    issues = near_duplicate_from_signatures(signatures, threshold=0.9)
    clusters = [set(i.affected_urls) for i in issues]
    assert {"https://x.com/a", "https://x.com/b"} in clusters
    # The unrelated page is not clustered with them.
    assert not any("https://x.com/c" in c for c in clusters)


def test_no_signatures_no_clusters():
    assert near_duplicate_from_signatures({}) == []
    assert near_duplicate_from_signatures({"u": _sig(BASE)}) == []  # need >= 2


def test_signature_is_json_serializable_shape():
    s = _sig(BASE)
    assert isinstance(s["minhash"], list) and s["shingle_count"] > 0
    assert all(isinstance(x, int) for x in s["minhash"])
