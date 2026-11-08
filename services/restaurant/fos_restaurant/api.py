from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from botocore.exceptions import ClientError
from pydantic import Field, StrictBool, model_validator

from fos_shared.contracts import (
    SOURCE_RESTAURANT,
    Model,
    PositiveInt,
    ProductChanged,
    ProductChangedData,
    new_message,
)
from fos_shared.dynamo import (
    client,
    condition_failed_indexes,
    is_conditional_check_failure,
    now_iso,
    outbox_put,
    plain,
    table_name,
    transact,
)
from fos_shared.http import ApiEvent, HttpError, api_handler, parse_body, path_param

Name = Annotated[str, Field(min_length=1)]
RestaurantStatus = Literal["OPEN", "CLOSED"]


class CreateRestaurantBody(Model):
    name: Name
    status: RestaurantStatus = "OPEN"


class UpdateRestaurantBody(Model):
    name: Name | None = None
    status: RestaurantStatus | None = None

    @model_validator(mode="after")
    def has_a_field(self) -> Self:
        if self.name is None and self.status is None:
            raise ValueError("Provide name or status")
        return self


class CreateProductBody(Model):
    name: Name
    price_minor: PositiveInt
    available: StrictBool = True


class UpdateProductBody(Model):
    name: Name | None = None
    price_minor: PositiveInt | None = None
    available: StrictBool | None = None

    @model_validator(mode="after")
    def has_a_field(self) -> Self:
        if self.name is None and self.price_minor is None and self.available is None:
            raise ValueError("Provide at least one field")
        return self


def _restaurant_key(restaurant_id: str) -> dict[str, str]:
    return {"PK": f"RESTAURANT#{restaurant_id}", "SK": "META"}


def _product_key(restaurant_id: str, product_id: str) -> dict[str, str]:
    return {"PK": f"RESTAURANT#{restaurant_id}", "SK": f"PRODUCT#{product_id}"}


def _without_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in ("PK", "SK")}


def _product_changed(product: dict[str, Any]) -> ProductChanged:
    return new_message(
        ProductChanged,
        source=SOURCE_RESTAURANT,
        correlation_id=product["productId"],
        causation_id=None,
        data=ProductChangedData(
            restaurant_id=product["restaurantId"],
            product_id=product["productId"],
            name=product["name"],
            price_minor=product["priceMinor"],
            version=product["version"],
        ),
    )


def create_restaurant(event: ApiEvent) -> tuple[int, Any]:
    body = parse_body(event, CreateRestaurantBody)
    restaurant_id = str(uuid4())
    item = {
        **_restaurant_key(restaurant_id),
        "restaurantId": restaurant_id,
        "name": body.name,
        "status": body.status,
        "createdAt": now_iso(),
    }
    client().put_item(TableName=table_name(), Item=item)
    return 201, _without_keys(item)


def update_restaurant(event: ApiEvent) -> tuple[int, Any]:
    restaurant_id = path_param(event, "restaurantId")
    fields = list(parse_body(event, UpdateRestaurantBody).model_dump(exclude_none=True).items())
    try:
        result = client().update_item(
            TableName=table_name(),
            Key=_restaurant_key(restaurant_id),
            UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeNames={f"#f{i}": key for i, (key, _) in enumerate(fields)},
            ExpressionAttributeValues={f":v{i}": value for i, (_, value) in enumerate(fields)},
            ReturnValues="ALL_NEW",
        )
    except ClientError as err:
        if is_conditional_check_failure(err):
            raise HttpError(404, "Restaurant not found") from err
        raise
    return 200, _without_keys(result["Attributes"])


def create_product(event: ApiEvent) -> tuple[int, Any]:
    table = table_name()
    restaurant_id = path_param(event, "restaurantId")
    body = parse_body(event, CreateProductBody)
    product_id = str(uuid4())
    product = {
        "productId": product_id,
        "restaurantId": restaurant_id,
        "name": body.name,
        "priceMinor": body.price_minor,
        "available": body.available,
        "version": 1,
    }
    try:
        transact(
            [
                {
                    "ConditionCheck": {
                        "TableName": table,
                        "Key": _restaurant_key(restaurant_id),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table,
                        "Item": {**_product_key(restaurant_id, product_id), **product},
                    }
                },
                outbox_put(table, _product_changed(product)),
            ]
        )
    except ClientError as err:
        if condition_failed_indexes(err) is not None:
            raise HttpError(404, "Restaurant not found") from err
        raise
    return 201, product


def update_product(event: ApiEvent) -> tuple[int, Any]:
    table = table_name()
    restaurant_id = path_param(event, "restaurantId")
    product_id = path_param(event, "productId")
    body = parse_body(event, UpdateProductBody)
    key = _product_key(restaurant_id, product_id)

    stored = client().get_item(TableName=table, Key=key, ConsistentRead=True).get("Item")
    if stored is None:
        raise HttpError(404, "Product not found")
    current = plain(stored)

    updated = {
        "productId": product_id,
        "restaurantId": restaurant_id,
        "name": body.name if body.name is not None else current["name"],
        "priceMinor": body.price_minor if body.price_minor is not None else current["priceMinor"],
        "available": body.available if body.available is not None else current["available"],
        "version": current["version"] + 1,
    }
    try:
        transact(
            [
                {
                    "Put": {
                        "TableName": table,
                        "Item": {**key, **updated},
                        "ConditionExpression": "#version = :current",
                        "ExpressionAttributeNames": {"#version": "version"},
                        "ExpressionAttributeValues": {":current": current["version"]},
                    }
                },
                outbox_put(table, _product_changed(updated)),
            ]
        )
    except ClientError as err:
        if condition_failed_indexes(err) is not None:
            raise HttpError(409, "Product was changed by another request; retry") from err
        raise
    return 200, updated


handler = api_handler(
    {
        "POST /restaurants": create_restaurant,
        "PATCH /restaurants/{restaurantId}": update_restaurant,
        "POST /restaurants/{restaurantId}/products": create_product,
        "PATCH /restaurants/{restaurantId}/products/{productId}": update_product,
    }
)
