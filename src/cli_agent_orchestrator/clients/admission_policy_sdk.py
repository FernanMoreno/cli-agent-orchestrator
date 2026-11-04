"""HTTP-only typed client for the authenticated admission-policy control API."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyRevision,
    AdmissionPolicyState,
    AdmissionPolicyUpdate,
)

_MAX_REVISION = 2**63 - 1
_BASE_PATH = "/admission/policy"
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


class AdmissionPolicySDKError(RuntimeError):
    """Base class for fixed, secret-safe policy client failures."""

    def __init__(self, message: str = "admission policy request failed") -> None:
        super().__init__(message)


class AdmissionPolicyCredentialError(AdmissionPolicySDKError):
    """The caller's token supplier did not provide a safe bearer value."""

    def __init__(self) -> None:
        super().__init__("admission policy credentials are unavailable")


class AdmissionPolicyTransportError(AdmissionPolicySDKError):
    """The HTTP transport failed; its error body and request are not retained."""

    def __init__(self) -> None:
        super().__init__("admission policy transport is unavailable")


class AdmissionPolicyProtocolError(AdmissionPolicySDKError):
    """The server response did not match the shared DTO contract."""

    def __init__(self) -> None:
        super().__init__("admission policy response is invalid")


class AdmissionPolicyHTTPError(AdmissionPolicySDKError):
    """An HTTP failure, retaining only the numeric status code."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("admission policy HTTP request failed")


class AdmissionPolicyAuthorizationError(AdmissionPolicyHTTPError):
    """The authenticated administrator was not authorized for this request."""


class AdmissionPolicyNotFoundError(AdmissionPolicyHTTPError):
    """The requested persisted policy revision does not exist."""


class AdmissionPolicyConflictError(AdmissionPolicyHTTPError):
    """The expected revision lost a compare-and-set race."""


class AdmissionPolicyValidationError(AdmissionPolicyHTTPError):
    """The server rejected the request DTO."""


class AdmissionPolicyUnavailableError(AdmissionPolicyHTTPError):
    """The policy control store or runtime is unavailable."""


_HTTP_ERRORS: Mapping[int, type[AdmissionPolicyHTTPError]] = {
    401: AdmissionPolicyAuthorizationError,
    403: AdmissionPolicyAuthorizationError,
    404: AdmissionPolicyNotFoundError,
    409: AdmissionPolicyConflictError,
    422: AdmissionPolicyValidationError,
    503: AdmissionPolicyUnavailableError,
}


class AdmissionPolicyClient:
    """Typed SDK using an injected synchronous HTTP client and token supplier.

    The caller retains ownership of ``client`` and its transport, timeout and
    lifecycle. Each operation asks ``token_supplier`` for a fresh bearer token.
    Endpoint paths are fixed, and response bodies are never copied into errors.
    """

    __slots__ = ("_client", "_token_supplier")

    def __init__(
        self,
        client: httpx.Client,
        token_supplier: Callable[[], str],
    ) -> None:
        if not isinstance(client, httpx.Client):
            raise TypeError("admission policy SDK requires an httpx.Client")
        if not callable(token_supplier):
            raise TypeError("admission policy SDK requires a token supplier")
        self._client = client
        self._token_supplier = token_supplier

    def get(self) -> AdmissionPolicyState:
        """Read persisted and separately applied policy revisions."""
        return self._request("GET", _BASE_PATH, AdmissionPolicyState)

    def update(self, update: AdmissionPolicyUpdate) -> AdmissionPolicyRecord:
        """Replace the full policy using the supplied expected revision."""
        if type(update) is not AdmissionPolicyUpdate:
            raise TypeError("admission policy update DTO required")
        return self._request(
            "PUT",
            _BASE_PATH,
            AdmissionPolicyRecord,
            body=update,
        )

    def revoke(self, revision: AdmissionPolicyRevision) -> AdmissionPolicyRecord:
        """Append a revocation using the supplied expected revision."""
        if type(revision) is not AdmissionPolicyRevision:
            raise TypeError("admission policy revision DTO required")
        return self._request(
            "POST",
            f"{_BASE_PATH}/revoke",
            AdmissionPolicyRecord,
            body=revision,
        )

    def seed(self, revision: AdmissionPolicyRevision) -> AdmissionPolicyRecord:
        """Explicitly seed from the server's configured source at revision zero."""
        if type(revision) is not AdmissionPolicyRevision:
            raise TypeError("admission policy revision DTO required")
        if revision.expected_revision != 0:
            raise ValueError("policy seed requires expected revision zero")
        return self._request(
            "POST",
            f"{_BASE_PATH}/seed",
            AdmissionPolicyRecord,
            body=revision,
        )

    def history(
        self,
        *,
        limit: int = 50,
        before_revision: int | None = None,
    ) -> AdmissionPolicyHistory:
        """Read one bounded keyset page of descending policy revisions."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("policy history limit must be an integer in [1, 100]")
        if before_revision is not None and (
            type(before_revision) is not int or not 1 <= before_revision <= _MAX_REVISION
        ):
            raise ValueError("policy history cursor must be a positive revision")
        params: dict[str, int] = {"limit": limit}
        if before_revision is not None:
            params["before_revision"] = before_revision
        return self._request(
            "GET",
            f"{_BASE_PATH}/history",
            AdmissionPolicyHistory,
            params=params,
        )

    def _token(self) -> str:
        try:
            token = self._token_supplier()
        except Exception:
            raise AdmissionPolicyCredentialError() from None
        if (
            type(token) is not str
            or not token
            or token != token.strip()
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in token)
        ):
            raise AdmissionPolicyCredentialError()
        return token

    def _request(
        self,
        method: str,
        path: str,
        response_model: type[_ResponseModel],
        *,
        body: BaseModel | None = None,
        params: Mapping[str, int] | None = None,
    ) -> _ResponseModel:
        token = self._token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            if body is None:
                response = self._client.request(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    follow_redirects=False,
                )
            else:
                response = self._client.request(
                    method,
                    path,
                    headers=headers,
                    json=body.model_dump(mode="json"),
                    params=params,
                    follow_redirects=False,
                )
        except httpx.RequestError:
            raise AdmissionPolicyTransportError() from None

        status_code = response.status_code
        if 300 <= status_code < 400:
            raise AdmissionPolicyProtocolError()
        if not 200 <= status_code < 300:
            error_type = _HTTP_ERRORS.get(status_code, AdmissionPolicyHTTPError)
            raise error_type(status_code)
        try:
            return response_model.model_validate_json(response.content)
        except (ValidationError, ValueError, UnicodeDecodeError):
            raise AdmissionPolicyProtocolError() from None
