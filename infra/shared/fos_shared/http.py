import base64
import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .dynamo import plain
from .log import log

ApiEvent = dict[str, Any]
Route = Callable[[ApiEvent], tuple[int, Any]]
B = TypeVar("B", bound=BaseModel)


class HttpError(Exception):
    def __init__(self, status_code: int, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details


def _respond(status_code: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(plain(body)),
    }


def parse_body(event: ApiEvent, model: type[B]) -> B:
    raw = event.get("body") or ""
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        payload = json.loads(raw)
    except ValueError as err:
        raise HttpError(400, "Request body must be valid JSON") from err
    try:
        return model.model_validate(payload)
    except ValidationError as err:
        details = json.loads(err.json(include_url=False, include_context=False))
        raise HttpError(400, "Invalid request", details) from err


def path_param(event: ApiEvent, name: str) -> str:
    value = (event.get("pathParameters") or {}).get(name)
    if not value:
        raise HttpError(400, f"Missing path parameter: {name}")
    return str(value)


def api_handler(routes: dict[str, Route]) -> Callable[[ApiEvent, Any], dict[str, Any]]:
    """Dispatches on API Gateway's routeKey, such as "POST /orders"."""

    def handler(event: ApiEvent, _context: Any = None) -> dict[str, Any]:
        route = routes.get(event.get("routeKey", ""))
        if route is None:
            return _respond(404, {"message": "Not found"})
        try:
            status_code, body = route(event)
            return _respond(status_code, body)
        except HttpError as err:
            return _respond(err.status_code, {"message": err.message, "details": err.details})
        except Exception as err:
            log("error", "Unhandled API error", routeKey=event.get("routeKey"), error=str(err))
            return _respond(500, {"message": "Internal server error"})

    return handler
