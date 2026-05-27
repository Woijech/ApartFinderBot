from datetime import UTC, datetime

from apartmentfinder.infrastructure.sources.realt.parser import (
    parse_realt_detail_page,
    parse_realt_search_page,
)

ROOM_HTML = """
<html><body>
  <main>
    <article>
      <a href="/rent/room-for-long/object/4119308/">Светлая комната у метро</a>
      <img src="/images/room.jpg">
      <div>451 р./мес.</div>
      <div>≈ 160 $/мес.</div>
      <div>Комната 55 м² 5/9 этаж</div>
      <div>г. Минск, ул. Слободская, 135</div>
      <div>Малиновка 15 минут</div>
      <p>Сдаётся отдельная непроходная комната девушке.</p>
      <span>7 часов назад ID 4119308</span>
    </article>
  </main>
</body></html>
"""


FLAT_HTML = """
<html><body>
  <section>
    <div>
      <a href="/rent/flat-for-long/object/4089354/">Стильная студия</a>
      <div>1 341 р./мес.</div>
      <div>≈ 480 $/мес.</div>
      <div>1 комн.30 м² 25/25 этаж</div>
      <div>г. Минск, ул. Брилевская, 37</div>
      <p>Уютная квартира с шикарным видом.</p>
      <span>08.05.2026 ID 4089354</span>
    </div>
  </section>
</body></html>
"""

LIVE_TEXT_HTML = """
<html><body>
  <main>
    <h1>96 объявлений</h1>
    <div>451 р./мес.</div>
    <div>≈ 160 $/мес.</div>
    <div>Комната 55 м² 5/9 этаж</div>
    <div>г. Минск, ул. Слободская, 135</div>
    <div>Показать больше</div>
    <div>Малиновка 15 минут</div>
    <div>Сдается комната на длительный срок.</div>
    <div>Контакты</div>
    <div>7 часов назад</div>
    <div>ID</div>
    <div>4119308</div>
    <div>282 р./мес.</div>
    <div>≈ 100 $/мес.</div>
    <div>Комната 18 м² 5/5 этаж</div>
    <div>г. Минск, ул. Голодеда</div>
    <div>26.09.2025 ID 3051236</div>
  </main>
</body></html>
"""


def test_parse_realt_room_search_page() -> None:
    result = parse_realt_search_page(
        ROOM_HTML,
        property_type="room",
        now=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
    )

    listing = result.listings[0]

    assert listing.source == "realt"
    assert listing.ad_id == 4119308
    assert listing.url == "https://realt.by/rent/room-for-long/object/4119308/"
    assert listing.price_usd == 160
    assert listing.price_byn == 451
    assert listing.rooms == "1"
    assert listing.area_m2 == 55
    assert listing.floor == "5"
    assert listing.total_floors == "9"
    assert listing.address == "г. Минск, ул. Слободская, 135"
    assert listing.metro == ["Малиновка 15 минут"]
    assert listing.images[0].gallery_url == "https://realt.by/images/room.jpg"


def test_parse_realt_flat_search_page() -> None:
    result = parse_realt_search_page(FLAT_HTML, property_type="apartment")

    listing = result.listings[0]

    assert listing.title == "Стильная студия"
    assert listing.price_usd == 480
    assert listing.rooms == "1"
    assert listing.area_m2 == 30
    assert listing.published_at == datetime(2026, 5, 7, 21, 0, tzinfo=UTC)


def test_parse_realt_prices_with_non_breaking_spaces() -> None:
    html = """
    <html><body>
      <article>
        <a href="/rent/flat-for-long/object/4089354/">Студия</a>
        <div>2\xa0795 р./мес.</div>
        <div>≈ 1\xa0000\xa0$/мес.</div>
        <div>1 комн.30 м² 25/25 этаж</div>
        <div>г. Минск, ул. Брилевская, 37</div>
        <span>08.05.2026 ID 4089354</span>
      </article>
    </body></html>
    """

    result = parse_realt_search_page(html, property_type="apartment")

    assert result.listings[0].price_byn == 2795
    assert result.listings[0].price_usd == 1000


def test_parse_realt_live_text_blocks_without_card_dom() -> None:
    result = parse_realt_search_page(
        LIVE_TEXT_HTML,
        property_type="room",
        now=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert result.total == 96
    assert [listing.ad_id for listing in result.listings] == [4119308, 3051236]
    first = result.listings[0]
    assert first.source == "realt"
    assert first.url == "https://realt.by/rent/room-for-long/object/4119308/"
    assert first.price_usd == 160
    assert first.price_byn == 451
    assert first.rooms == "1"
    assert first.address == "г. Минск, ул. Слободская, 135"
    assert first.metro == ["Малиновка 15 минут"]
    assert first.description == "Сдается комната на длительный срок."


def test_parse_realt_detail_page_merges_meta_data() -> None:
    fallback = parse_realt_search_page(ROOM_HTML, property_type="room").listings[0]
    detail_html = """
    <html>
      <head>
        <link rel="canonical" href="https://realt.by/rent/room-for-long/object/4119308/">
        <meta property="og:title" content="Комната с полным описанием">
        <meta name="description" content="Полное описание из detail страницы.">
        <meta property="og:image" content="https://img.realt.by/room.jpg">
      </head>
    </html>
    """

    listing = parse_realt_detail_page(detail_html, fallback)

    assert listing.title == "Комната с полным описанием"
    assert listing.description == "Полное описание из detail страницы."
    assert listing.images[0].gallery_url == "https://img.realt.by/room.jpg"
