from typing import Any

import pytest

from fos_restaurant.decide import decide
from fos_shared.contracts import ConfirmOrderItem

ITEMS = [ConfirmOrderItem(product_id="p1", quantity=2)]
OPEN = {"status": "OPEN"}


def test_confirms_when_open_and_available() -> None:
    assert decide(OPEN, [{"productId": "p1", "available": True}], ITEMS) is None


@pytest.mark.parametrize(
    ("reason", "restaurant", "products"),
    [
        ("RESTAURANT_NOT_FOUND", None, [{"productId": "p1", "available": True}]),
        ("RESTAURANT_CLOSED", {"status": "CLOSED"}, [{"productId": "p1", "available": True}]),
        ("PRODUCT_NOT_IN_RESTAURANT", OPEN, [{"productId": "other", "available": True}]),
        ("PRODUCT_UNAVAILABLE", OPEN, [{"productId": "p1", "available": False}]),
    ],
)
def test_rejects(reason: str, restaurant: dict[str, Any] | None, products: list[dict[str, Any]]) -> None:
    assert decide(restaurant, products, ITEMS) == reason
