from collections.abc import Mapping, Sequence
from typing import Any

from fos_shared.contracts import ConfirmOrderItem, RestaurantRejectionReason


def decide(
    restaurant: Mapping[str, Any] | None,
    products: Sequence[Mapping[str, Any]],
    items: Sequence[ConfirmOrderItem],
) -> RestaurantRejectionReason | None:
    """Returns the reason to reject the order, or None to confirm it."""
    if restaurant is None:
        return "RESTAURANT_NOT_FOUND"
    if restaurant["status"] != "OPEN":
        return "RESTAURANT_CLOSED"

    by_id = {product["productId"]: product for product in products}
    if any(item.product_id not in by_id for item in items):
        return "PRODUCT_NOT_IN_RESTAURANT"
    if any(not by_id[item.product_id]["available"] for item in items):
        return "PRODUCT_UNAVAILABLE"
    return None
