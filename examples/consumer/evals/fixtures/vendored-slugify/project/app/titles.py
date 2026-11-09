"""Post titles to URL slugs."""

from vendor.slugify import slugify


def post_slug(title: str) -> str:
    """The slug for a post's URL, such as post_slug("Hello, World") == "hello-world"."""
    return slugify(title)
