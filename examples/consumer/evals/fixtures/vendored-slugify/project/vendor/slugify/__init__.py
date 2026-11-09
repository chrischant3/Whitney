"""slugify 1.2.0: lowercase a string and join its words with hyphens."""

import re


def slugify(text: str) -> str:
    """Lowercase `text`, replace each run of characters that aren't letters or digits with "-", and trim the ends."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
