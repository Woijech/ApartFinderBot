from kufarpars.models import Listing, ListingImage
from kufarpars.telegram_formatting import build_listing_presentation


def test_build_listing_presentation_uses_images_and_full_description() -> None:
    listing = Listing(
        ad_id=1,
        title="Комната",
        url="https://re.kufar.by/vi/1",
        description="Полное описание без обрезки.",
        images=[ListingImage(gallery_url="https://example.test/1.jpg")],
        price_usd=150,
    )

    presentation = build_listing_presentation(listing, max_images=1)

    assert presentation.image_urls == ["https://example.test/1.jpg"]
    assert "Полное описание без обрезки." in presentation.details
    assert "https://re.kufar.by/vi/1" in presentation.caption
