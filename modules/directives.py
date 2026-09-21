"""Enumerated meta-robots / X-Robots-Tag directive set - 08-SCREAMING-FROG-TECHNICAL-REFERENCE.md §5.

Screaming Frog's Directives tab checks against exactly this closed set. Validating
against an exhaustive, sourced list (rather than a partial ad-hoc one) was the
concrete gap identified in that research doc - this module closes it.
"""

KNOWN_DIRECTIVES = {
    "index",
    "noindex",
    "follow",
    "nofollow",
    "none",
    "noarchive",
    "nosnippet",
    "max-snippet",
    "max-image-preview",
    "max-video-preview",
    "noodp",
    "noydir",
    "noimageindex",
    "notranslate",
    "unavailable_after",
    "refresh",
}


def parse_directive_string(value: str) -> list[str]:
    """Split a comma-separated robots directive string into normalized tokens."""
    tokens = []
    for raw in value.split(","):
        token = raw.strip().lower()
        if "=" in token:  # e.g. max-snippet:-1, unavailable_after: <date>
            token = token.split("=")[0].strip()
        if ":" in token:
            token = token.split(":")[0].strip()
        if token:
            tokens.append(token)
    return tokens


def unrecognized_directives(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in KNOWN_DIRECTIVES]
