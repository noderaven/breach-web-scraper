"""Registry of available breach scraper sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from breach_scraper.sources import wa_atg


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    display_name: str
    default_url: str
    fetch_html: Callable[..., str]
    parse_html: Callable[[str, str], list[dict[str, str]]]


SOURCES: dict[str, SourceDefinition] = {
    "wa_atg": SourceDefinition(
        key="wa_atg",
        display_name="Washington Attorney General",
        default_url=wa_atg.DEFAULT_URL,
        fetch_html=wa_atg.fetch_html,
        parse_html=wa_atg.parse_breach_table,
    ),
}


def get_source(key: str) -> SourceDefinition:
    try:
        return SOURCES[key]
    except KeyError as exc:
        available = ", ".join(sorted(SOURCES))
        raise ValueError(f"Unknown source '{key}'. Available sources: {available}") from exc


def list_sources() -> list[SourceDefinition]:
    return [SOURCES[key] for key in sorted(SOURCES)]
