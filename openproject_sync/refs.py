"""Work package references in free text, e.g. ``OP#EL-5`` or ``OP#1234``.

Pure Python (no Django imports), so it can be unit tested on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_PREFIX = "OP#"
DEFAULT_LINK_TEMPLATE = "{base}/work_packages/{id}"

# Project based ids (OpenProject >= 17.5): 'EL-5', 'ROS2-89'. Classic ids: '1234'
_ID_PATTERN = r"(?:[A-Za-z][A-Za-z0-9_]*-\d+|\d+)"

# Work package links pasted into a text / link field
_URL_PATTERN = re.compile(r"/(?:work_packages|wp)/(" + _ID_PATTERN + r")(?![\w-])")


@dataclass(frozen=True)
class WorkPackageRef:
    """A normalised work package reference."""

    key: str  # 'EL-5' or '1234', usable in API paths and URLs
    text: str  # Text as it appeared in the source, e.g. 'OP#EL-05'

    @property
    def is_numeric(self) -> bool:
        """True for classic instance wide numeric ids."""
        return self.key.isdigit()


def normalize_key(raw: str) -> str:
    """Normalise an id: upper case project prefix, no leading zeros ('el-05' -> 'EL-5')."""
    raw = raw.strip()
    if raw.isdigit():
        return str(int(raw))
    prefix, _, number = raw.rpartition("-")
    return f"{prefix.upper()}-{int(number)}"


def _ref_regex(prefix: str, include_link_text: bool = True) -> re.Pattern:
    # Not preceded by a word char or '/' (part of a URL), optionally not by '[' (link text)
    before = r"(?<![\w/])" if include_link_text else r"(?<![\w\[/])"
    return re.compile(
        before + re.escape(prefix) + r"(" + _ID_PATTERN + r")(?![\w-])",
        re.IGNORECASE,
    )


def find_refs(text: str, prefix: str = DEFAULT_PREFIX) -> list[WorkPackageRef]:
    """Return all unique references in a text, in order of appearance."""
    if not text:
        return []
    refs: dict[str, WorkPackageRef] = {}
    for match in _ref_regex(prefix).finditer(text):
        key = normalize_key(match.group(1))
        refs.setdefault(key, WorkPackageRef(key=key, text=match.group(0)))
    return list(refs.values())


def find_url_ref(text: str, base_url: str) -> WorkPackageRef | None:
    """Return the work package referenced by an OpenProject URL in ``text``, if any."""
    if not text or not base_url:
        return None
    host = re.sub(r"^https?://", "", base_url.rstrip("/"), flags=re.IGNORECASE)
    if host.lower() not in text.lower():
        return None
    match = _URL_PATTERN.search(text)
    if not match:
        return None
    return WorkPackageRef(key=normalize_key(match.group(1)), text=match.group(0))


def project_key(value: str) -> str:
    """Project identifier from an identifier or a project URL.

    'FER_27' -> 'FER_27', 'https://pm.example.com/projects/FER_27?jump=home' -> 'FER_27'
    """
    value = (value or "").strip()
    match = re.search(r"/projects/([^/?#]+)", value)
    return match.group(1) if match else value


def work_package_url(
    base_url: str, key: str, template: str = DEFAULT_LINK_TEMPLATE
) -> str:
    """Browser URL of a work package."""
    return template.format(base=base_url.rstrip("/"), id=key)


# Code blocks and inline code are skipped when linkifying markdown
_CODE = r"(```.*?```|~~~.*?~~~|`[^`\n]*`)"


def linkify_markdown(
    text: str,
    base_url: str,
    prefix: str = DEFAULT_PREFIX,
    template: str = DEFAULT_LINK_TEMPLATE,
) -> str:
    """Turn bare references in markdown into links: ``OP#EL-5`` -> ``[OP#EL-5](https://...)``.

    Idempotent: references that already are link text, part of a URL or inside
    code are left alone.
    """
    if not text or not base_url:
        return text

    ref = _ref_regex(prefix, include_link_text=False).pattern
    # Skip references that are the text of an existing link: '[see OP#EL-5](...)'
    pattern = re.compile(
        _CODE + r"|(" + ref + r"(?![^\[\n]*\]\())", re.IGNORECASE | re.DOTALL
    )

    def replace(match: re.Match) -> str:
        if match.group(1):
            return match.group(1)
        found = match.group(2)
        key = normalize_key(match.group(3))
        return f"[{found}]({work_package_url(base_url, key, template)})"

    return pattern.sub(replace, text)
