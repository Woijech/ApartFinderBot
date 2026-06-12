"""Source orchestration helpers that depend only on source protocols."""

from __future__ import annotations

import logging
from time import perf_counter

from apartmentfinder.application.ports import ListingSource
from apartmentfinder.domain.models import Listing, SearchRequest
from apartmentfinder.infrastructure.metrics import (
    inc_empty_result,
    inc_source_error,
    observe_source_response_time,
)

logger = logging.getLogger(__name__)


class SourceNetworkError(RuntimeError):
    """Raised when every configured listing source fails."""


def source_label(source: str) -> str:
    """Return a human label for one source code."""
    return {"kufar": "Kufar", "realt": "Realt"}.get(source, source)


def fetch_from_sources(
    request: SearchRequest,
    sources: list[ListingSource],
    *,
    max_pages: int,
    delay_seconds: float,
) -> list[Listing]:
    """Fetch search listings from all configured sources."""
    listings: list[Listing] = []
    failures = []
    for source in sources:
        started_at = perf_counter()
        logger.info(
            "listing_source_check_started source=%s max_pages=%s delay_seconds=%s",
            source.code,
            max_pages,
            delay_seconds,
        )
        try:
            source_listings = list(
                source.search_pages(
                    request,
                    max_pages=max_pages,
                    delay_seconds=delay_seconds,
                ),
            )
            listings.extend(source_listings)
            observe_source_response_time(
                (perf_counter() - started_at),
                source=source.code,
            )
            if not source_listings:
                inc_empty_result(source=source.code)
            logger.info(
                "listing_source_check_finished source=%s count=%s duration_ms=%s",
                source.code,
                len(source_listings),
                elapsed_ms(started_at),
            )
        except Exception as error:
            logger.warning(
                "listing_source_failed source=%s error_type=%s error=%s "
                "duration_ms=%s",
                source.code,
                type(error).__name__,
                error,
                elapsed_ms(started_at),
            )
            inc_source_error(source=source.code, error_type=type(error).__name__)
            failures.append(error)
        finally:
            source.close()
    if not listings and failures:
        raise SourceNetworkError("All listing sources failed") from failures[-1]
    logger.info(
        "listing_sources_finished total=%s sources=%s failures=%s",
        len(listings),
        source_counts(listings),
        len(failures),
    )
    return listings


def enrich_listing_details(
    listings: list[Listing],
    source_by_code: dict[str, ListingSource],
) -> list[Listing]:
    """Fetch detail pages with the source that owns each listing."""
    enriched = []
    try:
        for listing in listings:
            source = source_by_code.get(listing.source)
            if source is None:
                logger.warning(
                    "listing_enrichment_source_missing source=%s ad_id=%s",
                    listing.source,
                    listing.ad_id,
                )
                enriched.append(listing)
                continue
            started_at = perf_counter()
            logger.debug(
                "listing_enrichment_started source=%s ad_id=%s",
                listing.source,
                listing.ad_id,
            )
            try:
                enriched.append(source.fetch_listing_detail(listing))
                logger.debug(
                    "listing_enrichment_finished source=%s ad_id=%s duration_ms=%s",
                    listing.source,
                    listing.ad_id,
                    elapsed_ms(started_at),
                )
            except Exception:
                logger.exception(
                    "listing_enrichment_failed source=%s ad_id=%s",
                    listing.source,
                    listing.ad_id,
                )
                enriched.append(listing)
    finally:
        for source in source_by_code.values():
            source.close()
    return enriched


def elapsed_ms(started_at: float) -> int:
    """Return elapsed milliseconds from a perf-counter timestamp."""
    return int((perf_counter() - started_at) * 1000)


def source_counts(listings: list[Listing]) -> dict[str, int]:
    """Return listing counts grouped by source code."""
    counts: dict[str, int] = {}
    for listing in listings:
        counts[listing.source] = counts.get(listing.source, 0) + 1
    return counts
