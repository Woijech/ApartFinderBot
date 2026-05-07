"""Command-line interface for manual Kufar searches.

The CLI is mainly a development and debugging surface for the parser. The
Telegram bot uses the same ``KufarClient`` and ``SearchRequest`` underneath.
"""

import csv
import json
from argparse import ArgumentParser
from dataclasses import asdict
from sys import stdout

from kufarpars import __version__
from kufarpars.client import KufarClient, SearchRequest
from kufarpars.config import settings
from kufarpars.models import Listing


def build_parser() -> ArgumentParser:
    """Build the top-level argparse parser."""
    parser = ArgumentParser(prog="kufarpars")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--base-url",
        default=settings.base_url,
        help="Kufar base URL.",
    )
    subparsers = parser.add_subparsers(dest="command")

    search_parser = subparsers.add_parser("search", help="Search realty listings.")
    search_parser.add_argument("--city", default="minsk", help="City slug.")
    search_parser.add_argument(
        "--deal",
        choices=["rent", "buy"],
        default="rent",
        help="Deal type.",
    )
    search_parser.add_argument(
        "--type",
        choices=["apartment", "room"],
        default="apartment",
        dest="property_type",
        help="Property type.",
    )
    search_parser.add_argument(
        "--rooms",
        type=int,
        choices=[1, 2, 3, 4],
        help="Number of rooms.",
    )
    search_parser.add_argument("--min-price", type=int, help="Minimum price.")
    search_parser.add_argument("--max-price", type=int, help="Maximum price.")
    search_parser.add_argument("--currency", default="USD", help="Price currency.")
    search_parser.add_argument("--text", help="Text search query.")
    search_parser.add_argument(
        "--sort",
        choices=["newest", "cheap", "expensive"],
        default="newest",
        help="Sort order.",
    )
    search_parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="How many result pages to fetch.",
    )
    search_parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between pages, seconds.",
    )
    search_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Raw Kufar query parameter, can be used more than once.",
    )
    search_parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format.",
    )
    return parser


def main() -> None:
    """Run the command-line entry point."""
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "search":
        request = SearchRequest(
            city=args.city,
            deal=args.deal,
            property_type=args.property_type,
            rooms=args.rooms,
            min_price=args.min_price,
            max_price=args.max_price,
            currency=args.currency,
            text=args.text,
            sort=args.sort,
            extra_params=_parse_extra_params(args.param),
        )
        with KufarClient() as client:
            listings = list(
                client.search_pages(
                    request,
                    max_pages=max(args.pages, 1),
                    delay_seconds=max(args.delay, 0),
                )
            )
        _print_listings(listings, args.format)
        return

    parser.print_help()


def _parse_extra_params(values: list[str]) -> dict[str, str]:
    """Parse raw ``KEY=VALUE`` query parameters from CLI flags."""
    result: dict[str, str] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key:
            raise ValueError(f"Invalid --param value: {value!r}. Use KEY=VALUE.")
        result[key] = raw
    return result


def _print_listings(listings: list[Listing], output_format: str) -> None:
    """Print listings in the requested output format."""
    if output_format == "json":
        data = [_listing_dict(item) for item in listings]
        print(json.dumps(data, ensure_ascii=False))
        return
    if output_format == "csv":
        _write_csv(listings)
        return
    _write_table(listings)


def _listing_dict(listing: Listing) -> dict[str, object]:
    """Convert a Listing dataclass into a JSON-friendly dictionary."""
    data = asdict(listing)
    data["published_at"] = (
        listing.published_at.isoformat() if listing.published_at else None
    )
    data.pop("raw_parameters", None)
    return data


def _write_csv(listings: list[Listing]) -> None:
    """Write listings to stdout as CSV."""
    fieldnames = [
        "ad_id",
        "title",
        "price_usd",
        "price_byn",
        "rooms",
        "area_m2",
        "floor",
        "total_floors",
        "address",
        "metro",
        "url",
    ]
    writer = csv.DictWriter(stdout, fieldnames=fieldnames)
    writer.writeheader()
    for listing in listings:
        row = _listing_dict(listing)
        row["metro"] = ", ".join(listing.metro)
        writer.writerow({field: row.get(field) for field in fieldnames})


def _write_table(listings: list[Listing]) -> None:
    """Write listings to stdout as a readable text table."""
    if not listings:
        print("Ничего не найдено.")
        return

    for index, listing in enumerate(listings, start=1):
        specs = _listing_specs(listing)
        print(f"{index}. {listing.price_label} | {listing.title}")
        if specs:
            print(f"   {specs}")
        if listing.short_location:
            print(f"   {listing.short_location}")
        if listing.description:
            print(f"   {listing.description[:180].strip()}")
        print(f"   {listing.url}")


def _listing_specs(listing: Listing) -> str:
    """Build a short room/area/floor summary for CLI output."""
    parts = []
    if listing.rooms:
        parts.append(f"{listing.rooms} комн.")
    if listing.area_m2:
        parts.append(f"{listing.area_m2:g} м2")
    if listing.floor:
        floor = f"этаж {listing.floor}"
        if listing.total_floors:
            floor = f"{floor} из {listing.total_floors}"
        parts.append(floor)
    return ", ".join(parts)


if __name__ == "__main__":
    main()
