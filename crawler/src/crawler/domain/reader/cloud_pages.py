from __future__ import annotations

import re
import unicodedata

from crawler.domain.reader.models import LocalResult


def useful_text(value: str) -> bool:
    value = unicodedata.normalize("NFC", value or "")
    return bool(re.search(r"[\w\d]", value, flags=re.UNICODE))


def cloud_part_indices(local: LocalResult) -> list[int]:
    return [p.index for p in local.parts if not useful_text(p.text) and p.has_visual_content]
