from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    description: str
    visible_text: str


class _PageParser(HTMLParser):
    def __init__(self, text_limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.text_limit = text_limit
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.description = ""
        self._ignored_depth = 0
        self._in_title = False
        self._text_size = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "template", "svg"}:
            self._ignored_depth += 1
        if lowered == "title":
            self._in_title = True
        if lowered == "meta":
            values = {key.lower(): value or "" for key, value in attrs}
            name = values.get("name", "").lower()
            prop = values.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.description = values.get("content", "")[:1000].strip()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "template", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        if not self._ignored_depth and self._text_size < self.text_limit:
            remaining = self.text_limit - self._text_size
            value = cleaned[:remaining]
            self.text_parts.append(value)
            self._text_size += len(value) + 1


def extract_page(html: str, text_limit: int = 50_000) -> ExtractedPage:
    parser = _PageParser(text_limit)
    parser.feed(html)
    parser.close()
    return ExtractedPage(
        title=" ".join(parser.title_parts)[:500],
        description=parser.description,
        visible_text=" ".join(parser.text_parts)[:text_limit],
    )
