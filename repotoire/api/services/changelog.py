"""Changelog service utilities.

This module provides utility functions for changelog management:
- Slug generation with uniqueness checking
- RSS feed generation (RFC 4287 compliant)
- Markdown rendering with sanitization

Usage:
    from repotoire.api.services.changelog import (
        generate_unique_slug,
        generate_rss_feed,
        render_markdown_safe,
    )
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element, SubElement, tostring

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from repotoire.db.models.changelog import ChangelogEntry

logger = get_logger(__name__)

# Base URL for links in RSS feed (configurable via environment)
import os

BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://repotoire.io")
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.repotoire.io")


# =============================================================================
# Slug Generation
# =============================================================================


async def generate_unique_slug(
    db: AsyncSession,
    title: str,
    max_length: int = 200,
) -> str:
    """Generate a unique, URL-friendly slug from a title.

    Args:
        db: Database session for uniqueness checking
        title: The title to convert to a slug
        max_length: Maximum length of the base slug

    Returns:
        A unique slug string

    Example:
        >>> await generate_unique_slug(db, "SSO/SAML Support")
        "sso-saml-support"
        >>> await generate_unique_slug(db, "SSO/SAML Support")  # If exists
        "sso-saml-support-1"
    """
    from repotoire.db.models.changelog import ChangelogEntry

    # Generate base slug
    base_slug = slugify(title, max_length=max_length)

    # Check if slug exists
    slug = base_slug
    counter = 1

    while True:
        result = await db.execute(
            select(ChangelogEntry.id).where(ChangelogEntry.slug == slug)
        )
        if not result.scalar_one_or_none():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


def generate_slug_sync(title: str, existing_slugs: set[str], max_length: int = 200) -> str:
    """Generate a unique slug synchronously.

    For use in Celery tasks where async is not available.

    Args:
        title: The title to convert to a slug
        existing_slugs: Set of existing slugs to check against
        max_length: Maximum length of the base slug

    Returns:
        A unique slug string
    """
    base_slug = slugify(title, max_length=max_length)
    slug = base_slug
    counter = 1

    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1

    return slug


# =============================================================================
# RSS Feed Generation
# =============================================================================


def generate_rss_feed(entries: list[ChangelogEntry]) -> str:
    """Generate a valid RSS 2.0 XML feed from changelog entries.

    Args:
        entries: List of published ChangelogEntry objects

    Returns:
        RSS 2.0 XML string

    Example output:
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
          <channel>
            <title>Repotoire Changelog</title>
            ...
          </channel>
        </rss>
    """
    # Create RSS root element
    rss = Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

    # Create channel element
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "Repotoire Changelog"
    SubElement(channel, "link").text = f"{BASE_URL}/changelog"
    SubElement(channel, "description").text = (
        "Latest updates, features, and release notes from Repotoire - "
        "the graph-powered code intelligence platform."
    )
    SubElement(channel, "language").text = "en-us"

    # Add last build date
    SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )

    # Add atom:link for self-reference (required for valid RSS)
    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", f"{API_BASE_URL}/api/v1/changelog/rss")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Add items
    for entry in entries:
        item = SubElement(channel, "item")

        # Title with version prefix if available
        title = f"{entry.version} - {entry.title}" if entry.version else entry.title
        SubElement(item, "title").text = title

        # Link to changelog entry
        entry_url = f"{BASE_URL}/changelog/{entry.slug}"
        SubElement(item, "link").text = entry_url

        # Description (summary, with CDATA wrapper for safety)
        desc = SubElement(item, "description")
        desc.text = entry.summary

        # GUID (permanent link)
        guid = SubElement(item, "guid")
        guid.set("isPermaLink", "true")
        guid.text = entry_url

        # Category
        SubElement(item, "category").text = entry.category.value

        # Publication date (RFC 2822 format)
        if entry.published_at:
            SubElement(item, "pubDate").text = format_datetime(entry.published_at)

    # Convert to XML string
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return xml_declaration + tostring(rss, encoding="unicode")


# =============================================================================
# Markdown Rendering
# =============================================================================


def render_markdown_safe(content: str) -> str:
    """Render Markdown to sanitized HTML.

    Renders Markdown content to HTML and sanitizes the output to prevent
    XSS attacks. Supports syntax highlighting for code blocks.

    Args:
        content: Markdown content to render

    Returns:
        Sanitized HTML string

    Note:
        Requires optional dependencies: markdown, bleach, pygments
        Install with: pip install markdown bleach pygments
    """
    try:
        import bleach
        import markdown
        from markdown.extensions.codehilite import CodeHiliteExtension
        from markdown.extensions.fenced_code import FencedCodeExtension
        from markdown.extensions.tables import TableExtension
        from markdown.extensions.toc import TocExtension
    except ImportError as e:
        logger.warning(
            f"Markdown rendering dependencies not installed: {e}. "
            "Install with: pip install markdown bleach pygments"
        )
        # Return escaped content as fallback
        import html
        return f"<pre>{html.escape(content)}</pre>"

    # Configure Markdown extensions
    extensions = [
        FencedCodeExtension(),
        CodeHiliteExtension(css_class="highlight", linenums=False),
        TableExtension(),
        TocExtension(permalink=True),
        "nl2br",  # Convert newlines to <br>
    ]

    # Render Markdown to HTML
    md = markdown.Markdown(extensions=extensions)
    html_content = md.convert(content)

    # Allowed HTML tags for sanitization
    allowed_tags = [
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p", "br", "hr",
        "ul", "ol", "li",
        "pre", "code", "blockquote",
        "strong", "em", "b", "i", "u", "s", "strike",
        "a", "img",
        "table", "thead", "tbody", "tr", "th", "td",
        "div", "span",
        # Code highlighting classes
        "highlight",
    ]

    # Allowed attributes
    allowed_attributes = {
        "a": ["href", "title", "target", "rel"],
        "img": ["src", "alt", "title", "width", "height"],
        "code": ["class"],
        "pre": ["class"],
        "div": ["class", "id"],
        "span": ["class"],
        "th": ["colspan", "rowspan"],
        "td": ["colspan", "rowspan"],
    }

    # Protocol whitelist for href/src attributes
    allowed_protocols = ["http", "https", "mailto"]

    # Sanitize HTML
    clean_html = bleach.clean(
        html_content,
        tags=allowed_tags,
        attributes=allowed_attributes,
        protocols=allowed_protocols,
        strip=True,
    )

    return clean_html


def get_plain_text_summary(content: str, max_length: int = 200) -> str:
    """Extract plain text summary from Markdown content.

    Useful for generating meta descriptions or email previews.

    Args:
        content: Markdown content
        max_length: Maximum length of summary

    Returns:
        Plain text summary
    """
    # Remove Markdown formatting
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", content)
    text = re.sub(r"`[^`]+`", "", text)

    # Remove headers
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)

    # Remove links but keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove images
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)

    # Remove emphasis markers
    text = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text)

    # Remove blockquotes
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)

    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}$", "", text, flags=re.MULTILINE)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Truncate if needed
    if len(text) > max_length:
        text = text[:max_length - 3].rsplit(" ", 1)[0] + "..."

    return text


# =============================================================================
# JSON-LD Structured Data (SEO)
# =============================================================================


def generate_json_ld(entry: ChangelogEntry) -> dict:
    """Generate JSON-LD structured data for SEO.

    Creates schema.org NewsArticle markup for changelog entries.

    Args:
        entry: ChangelogEntry to generate structured data for

    Returns:
        Dictionary suitable for embedding as JSON-LD script
    """
    data = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": entry.title,
        "description": entry.summary,
        "url": f"{BASE_URL}/changelog/{entry.slug}",
        "datePublished": entry.published_at.isoformat() if entry.published_at else None,
        "dateModified": entry.updated_at.isoformat() if entry.updated_at else None,
        "publisher": {
            "@type": "Organization",
            "name": "Repotoire",
            "url": BASE_URL,
            "logo": {
                "@type": "ImageObject",
                "url": f"{BASE_URL}/logo.png",
            },
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": f"{BASE_URL}/changelog/{entry.slug}",
        },
    }

    # Add image if available
    if entry.image_url:
        data["image"] = entry.image_url

    # Add author if available
    if entry.author:
        data["author"] = {
            "@type": "Person",
            "name": entry.author.name or "Repotoire Team",
        }
    else:
        data["author"] = {
            "@type": "Organization",
            "name": "Repotoire Team",
        }

    # Add article section based on category
    category_labels = {
        "feature": "New Features",
        "improvement": "Improvements",
        "fix": "Bug Fixes",
        "breaking": "Breaking Changes",
        "security": "Security Updates",
        "deprecation": "Deprecations",
    }
    data["articleSection"] = category_labels.get(entry.category.value, "Updates")

    return data
