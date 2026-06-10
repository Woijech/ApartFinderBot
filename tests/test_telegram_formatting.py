from datetime import UTC, datetime

from apartmentfinder.domain.models import Listing, ListingImage
from apartmentfinder.interfaces.telegram.formatting import build_listing_presentation


def test_build_listing_presentation_uses_images_and_full_description() -> None:
    listing = Listing(
        ad_id=1,
        title="Комната",
        url="https://re.kufar.by/vi/1",
        description="Полное описание без обрезки.",
        images=[ListingImage(gallery_url="https://example.test/1.jpg")],
        price_usd=150,
        seller_name="Агент",
        published_at=datetime(2026, 5, 7, 20, 23, tzinfo=UTC),
    )

    presentation = build_listing_presentation(listing, max_images=1)

    assert presentation.image_urls == ["https://example.test/1.jpg"]
    assert "Полное описание без обрезки." in presentation.caption
    assert "https://re.kufar.by/vi/1" in presentation.caption
    assert presentation.details is None
    assert "Контакт" not in presentation.caption
    assert "Агент" not in presentation.caption
    assert "07.05.2026 23:23" in presentation.caption
    assert "Источник" in presentation.caption
    assert "Kufar" in presentation.caption


def test_build_listing_presentation_without_images_uses_message_limit() -> None:
    listing = Listing(
        ad_id=2,
        title="Квартира без фото",
        url="https://re.kufar.by/vi/2",
        description="Очень длинное описание. " * 400,
        price_usd=350,
    )

    presentation = build_listing_presentation(listing, max_images=0)

    assert presentation.image_urls == []
    assert presentation.details is None
    assert len(presentation.caption) <= 4096
    assert "..." in presentation.caption
    assert presentation.caption.endswith("🔗 <b>Объявление:</b> https://re.kufar.by/vi/2")


def test_build_listing_presentation_with_images_uses_caption_limit() -> None:
    listing = Listing(
        ad_id=3,
        title="Комната с фото",
        url="https://re.kufar.by/vi/3",
        description="Очень длинное описание. " * 200,
        images=[ListingImage(gallery_url="https://example.test/3.jpg")],
        price_usd=200,
    )

    presentation = build_listing_presentation(listing, max_images=1)

    assert presentation.image_urls == ["https://example.test/3.jpg"]
    assert len(presentation.caption) <= 1024
    assert "..." in presentation.caption


def test_build_listing_presentation_trims_description_before_listing_url() -> None:
    listing = Listing(
        ad_id=4,
        title="Комната с очень длинным описанием",
        url="https://re.kufar.by/vi/4",
        description="Очень длинное описание. " * 200,
        images=[ListingImage(gallery_url="https://example.test/4.jpg")],
        price_usd=200,
    )

    presentation = build_listing_presentation(listing, max_images=1)

    assert len(presentation.caption) <= 1024
    assert "..." in presentation.caption
    assert presentation.caption.endswith("🔗 <b>Объявление:</b> https://re.kufar.by/vi/4")
