"""File names for downloads. Every name built from the user's text follows one rule, so a
saved file is named as the node, basis set or investigation is: only the characters Windows
refuses in a file name are replaced."""

import re
from urllib.parse import quote

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def file_name(text: str, default: str) -> str:
    """`text` as a file name: each of <>:"/\\|?* and each control character becomes "_",
    spaces around it and dots at its end go (Windows drops those). Everything else stays:
    primes, spaces, Greek letters, en dashes. `default` when nothing is left."""
    return _FORBIDDEN.sub("_", text).strip(" ").rstrip(". ") or default


def attachment(name: str) -> str:
    """A Content-Disposition value for a download named `name`. Headers are Latin-1 (a Greek
    letter in a plain filename gave a 500), so the plain filename is ASCII and filename*
    carries the real name, which browsers use."""
    fallback = "".join(c if c.isascii() else "_" for c in name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"
