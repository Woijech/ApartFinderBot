"""Extensible search-target and filter catalog for bot UI.

Add new categories here first. The Telegram bot reads this catalog to render
buttons, while ``SearchRequest`` remains a generic transport object.
"""

from __future__ import annotations

from dataclasses import dataclass

from kufarpars.client import SearchRequest


@dataclass(frozen=True)
class SearchTarget:
    """A parser target that can be selected in the bot UI."""

    code: str
    title: str
    request_patch: dict[str, object]
    description: str


@dataclass(frozen=True)
class PriceRange:
    """A user-facing price preset for the current bot flow."""

    code: str
    title: str
    min_price: int | None
    max_price: int | None


@dataclass(frozen=True)
class SimpleOption:
    """A compact selectable filter option."""

    code: str
    title: str
    value: object


SEARCH_TARGETS = [
    SearchTarget(
        code="apartment",
        title="Квартира",
        request_patch={"property_type": "apartment"},
        description="Аренда квартир в Минске",
    ),
    SearchTarget(
        code="room",
        title="Комната",
        request_patch={"property_type": "room"},
        description="Аренда комнат в Минске",
    ),
]

PRICE_RANGES = [
    PriceRange("any", "Любая цена", None, None),
    PriceRange("0_150", "до 150 $", 0, 150),
    PriceRange("150_250", "150-250 $", 150, 250),
    PriceRange("250_350", "250-350 $", 250, 350),
    PriceRange("350_500", "350-500 $", 350, 500),
    PriceRange("500_800", "500-800 $", 500, 800),
    PriceRange("800_1200", "800-1200 $", 800, 1200),
]

ROOM_OPTIONS = [
    SimpleOption("any", "Любое", None),
    SimpleOption("1", "1 комната", 1),
    SimpleOption("2", "2 комнаты", 2),
    SimpleOption("3", "3 комнаты", 3),
    SimpleOption("4", "4 комнаты", 4),
]

DISTRICT_OPTIONS = [
    SimpleOption("any", "Любой район", None),
    SimpleOption("centralny", "Центральный", "Центральный"),
    SimpleOption("sovetsky", "Советский", "Советский"),
    SimpleOption("pervomaysky", "Первомайский", "Первомайский"),
    SimpleOption("partizansky", "Партизанский", "Партизанский"),
    SimpleOption("zavodskoy", "Заводской", "Заводской"),
    SimpleOption("leninsky", "Ленинский", "Ленинский"),
    SimpleOption("oktyabrsky", "Октябрьский", "Октябрьский"),
    SimpleOption("moskovsky", "Московский", "Московский"),
    SimpleOption("frunzensky", "Фрунзенский", "Фрунзенский"),
]

METRO_OPTIONS = [
    SimpleOption("any", "Любое метро", None),
    SimpleOption("kamennaya_gorka", "Каменная Горка", "Каменная Горка"),
    SimpleOption("kuncevshchina", "Кунцевщина", "Кунцевщина"),
    SimpleOption("sportivnaya", "Спортивная", "Спортивная"),
    SimpleOption("pushkinskaya", "Пушкинская", "Пушкинская"),
    SimpleOption("molodezhnaya", "Молодёжная", "Молодёжная"),
    SimpleOption("frunzenskaya", "Фрунзенская", "Фрунзенская"),
    SimpleOption("nemiga", "Немига", "Немига"),
    SimpleOption("oktyabrskaya", "Октябрьская", "Октябрьская"),
    SimpleOption("ploshchad_pobedy", "Площадь Победы", "Площадь Победы"),
    SimpleOption("yakuba_kolasa", "Площадь Якуба Коласа", "Площадь Якуба Коласа"),
]


def default_request() -> SearchRequest:
    """Return the default bot search request."""
    return SearchRequest(city="minsk", deal="rent", currency="USD", sort="newest")


def target_by_code(code: str) -> SearchTarget:
    """Find a search target by internal code."""
    for target in SEARCH_TARGETS:
        if target.code == code:
            return target
    raise ValueError(f"Unknown search target: {code}")


def target_for_request(request: SearchRequest) -> SearchTarget:
    """Find the catalog target matching a request."""
    for target in SEARCH_TARGETS:
        if target.request_patch.get("property_type") == request.property_type:
            return target
    return SEARCH_TARGETS[0]


def price_range_by_code(code: str) -> PriceRange:
    """Find a price preset by callback code."""
    for price_range in PRICE_RANGES:
        if price_range.code == code:
            return price_range
    raise ValueError(f"Unknown price range: {code}")


def room_option_by_code(code: str) -> SimpleOption:
    """Find a room option by callback code."""
    return _option_by_code(ROOM_OPTIONS, code, "room")


def district_option_by_code(code: str) -> SimpleOption:
    """Find a district option by callback code."""
    return _option_by_code(DISTRICT_OPTIONS, code, "district")


def metro_option_by_code(code: str) -> SimpleOption:
    """Find a metro option by callback code."""
    return _option_by_code(METRO_OPTIONS, code, "metro")


def _option_by_code(
    options: list[SimpleOption],
    code: str,
    label: str,
) -> SimpleOption:
    """Find one option by code or raise a helpful error."""
    for option in options:
        if option.code == code:
            return option
    raise ValueError(f"Unknown {label} option: {code}")
