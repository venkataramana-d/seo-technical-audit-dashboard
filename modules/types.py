"""Shared types for the per-page audit pipeline - 02-AUDIT-ENGINE.md §1."""
from dataclasses import dataclass, field


@dataclass
class AuditIssue:
    issue_type: str
    category: str  # one of the 11 scoring categories, see scoring.py
    severity: str  # error|warning|notice - 02-AUDIT-ENGINE.md §3
    impact_score: int  # 1-10
    effort_level: str  # low|medium|high
    what: str
    why: str
    root_cause: str
    fix: str

    def to_explanation_json(self) -> dict:
        return {"what": self.what, "why": self.why, "root_cause": self.root_cause, "fix": self.fix}


@dataclass
class ImageFact:
    src: str
    alt: str | None
    has_alt: bool


@dataclass
class HreflangFact:
    href: str
    lang: str


@dataclass
class PageFacts:
    url: str
    status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    h1_tags: list[str] = field(default_factory=list)
    canonical_url: str | None = None
    robots_meta: list[str] = field(default_factory=list)
    x_robots_tag: list[str] = field(default_factory=list)
    charset: str | None = None
    viewport_meta: str | None = None
    favicon_present: bool = False
    og_tags: dict[str, str] = field(default_factory=dict)
    twitter_tags: dict[str, str] = field(default_factory=dict)
    schema_blocks: list[dict] = field(default_factory=list)
    schema_parse_errors: int = 0
    hreflang: list[HreflangFact] = field(default_factory=list)
    images: list[ImageFact] = field(default_factory=list)
    word_count: int = 0
    content_hash: str | None = None
    # {"minhash": [...], "shingle_count": n} for near-duplicate detection, or None
    # for empty/near-empty content. JSON-ready so it can be persisted directly.
    content_signature: dict | None = None
