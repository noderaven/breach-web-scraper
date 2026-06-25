"""Registry of available breach scraper sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from breach_scraper.sources import ca_oag, hhs_ocr, maine_ag, or_doj, wa_atg


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
    "ca_oag": SourceDefinition(
        key="ca_oag",
        display_name="California Attorney General",
        default_url=ca_oag.DEFAULT_URL,
        fetch_html=ca_oag.fetch_html,
        parse_html=ca_oag.parse_breach_table,
    ),
    "or_doj": SourceDefinition(
        key="or_doj",
        display_name="Oregon Department of Justice",
        default_url=or_doj.DEFAULT_URL,
        fetch_html=or_doj.fetch_html,
        parse_html=or_doj.parse_breach_table,
    ),
    "maine_ag": SourceDefinition(
        key="maine_ag",
        display_name="Maine Attorney General",
        default_url=maine_ag.DEFAULT_URL,
        fetch_html=maine_ag.fetch_html,
        parse_html=maine_ag.parse_breach_table,
    ),
    "hhs_ocr": SourceDefinition(
        key="hhs_ocr",
        display_name="HHS OCR Breach Portal",
        default_url=hhs_ocr.DEFAULT_URL,
        fetch_html=hhs_ocr.fetch_html,
        parse_html=hhs_ocr.parse_breach_table,
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
