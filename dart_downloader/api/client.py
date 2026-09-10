from __future__ import annotations

import httpx

from dart_downloader.models.models import OrderData, OrderResponse

DEFAULT_HOST = "ordering.diversityarrays.com"
ORDERS_PATH = "/dart/orders"


def base_url_for_host(host: str) -> str:
    """Build the orders API base URL from a host.

    Accepts a bare host (``ordering.diversityarrays.com``) or a full URL
    (``https://staging.example.com``); returns the orders endpoint base, e.g.
    ``https://ordering.diversityarrays.com/dart/orders``. Defaults to https
    when no scheme is given.
    """
    host = (host or DEFAULT_HOST).strip().rstrip("/")
    if "://" not in host:
        host = f"https://{host}"
    return f"{host}{ORDERS_PATH}"


# Default orders API base URL.
BASE_URL = base_url_for_host(DEFAULT_HOST)


class DartApiClient:
    def __init__(
        self,
        share_token: str,
        timeout: float = 30.0,
        base_url: str | None = None,
    ):
        self._share_token = share_token
        self._base_url = (base_url or BASE_URL).rstrip("/")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {share_token}"},
            timeout=timeout,
            follow_redirects=True,
        )

    def get_order(self, order_number: str) -> OrderData:
        url = f"{self._base_url}/{order_number}"
        response = self._client.get(url)
        response.raise_for_status()
        order_response = OrderResponse.model_validate(response.json())
        if not order_response.data:
            raise ValueError(f"No data returned for order {order_number}")
        return order_response.data[0]

    @property
    def client(self) -> httpx.Client:
        return self._client

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
