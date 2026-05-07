from kufarpars.client import SearchRequest


def test_search_request_builds_rent_url_parts() -> None:
    request = SearchRequest(rooms=2, max_price=500, text="возле метро")

    assert request.path() == "/l/minsk/snyat/kvartiru/2k"
    assert request.params()["prc"] == "r:0,500"
    assert request.params()["query"] == "возле метро"


def test_search_request_builds_room_url_parts() -> None:
    request = SearchRequest(property_type="room", rooms=2)

    assert request.path() == "/l/minsk/snyat/komnatu"


def test_search_request_keeps_local_only_filters_out_of_kufar_params() -> None:
    request = SearchRequest(
        district="Центральный",
        metro="Немига",
        include_keywords=["без хозяев"],
        exclude_keywords=["койко-место"],
    )

    params = request.params()

    assert "district" not in params
    assert "metro" not in params
    assert "include_keywords" not in params
    assert "exclude_keywords" not in params
