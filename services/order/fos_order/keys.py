# Only in-progress orders carry sweepPk and sweepSk, which keeps the sweeper's GSI small.
ACTIVE_ORDERS_PK = "ACTIVE"


def order_key(order_id: str) -> dict[str, str]:
    return {"PK": f"ORDER#{order_id}", "SK": "META"}


def step_key(order_id: str, seq: int) -> dict[str, str]:
    return {"PK": f"ORDER#{order_id}", "SK": f"STEP#{seq:06d}"}


def catalog_key(restaurant_id: str, product_id: str) -> dict[str, str]:
    return {"PK": f"CATALOG#{restaurant_id}", "SK": f"PRODUCT#{product_id}"}
