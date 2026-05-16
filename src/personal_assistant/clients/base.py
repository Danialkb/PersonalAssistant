from typing import Any

import httpx


class HttpApiClient:
    service_name = "API"

    def __init__(self, base_url: str, *, timeout: float = 20) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: httpx.Auth | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("GET", path, params=params, auth=auth, headers=headers)

    def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        auth: httpx.Auth | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("POST", path, json=json, auth=auth, headers=headers)

    def _put(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        auth: httpx.Auth | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("PUT", path, json=json, auth=auth, headers=headers)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: httpx.Auth | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        response = httpx.request(
            method,
            f"{self._base_url}{path}",
            params=params,
            json=json,
            auth=auth,
            headers=headers,
            timeout=self._timeout,
        )
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            details = self._response_error_details(response)
            message = str(exc)
            if details:
                message = f"{message}\n{self.service_name} response: {details}"
            raise httpx.HTTPStatusError(
                message, request=exc.request, response=exc.response
            ) from exc

    def _response_error_details(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip()
        return str(payload)
