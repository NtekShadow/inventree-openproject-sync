"""Minimal OpenProject API v3 client."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger("inventree")


class OpenProjectError(Exception):
    """Raised for failed OpenProject requests."""

    def __init__(
        self, message: str, status: int | None = None, body: dict | None = None
    ):
        super().__init__(message)
        self.status = status
        self.body = body or {}

    def invalid_attributes(self) -> set[str]:
        """Attribute names rejected by a 422 response ('startDate', 'status', ...)."""
        errors = self.body.get("_embedded", {}).get("errors") or [self.body]
        attrs = set()
        for err in errors:
            attr = err.get("_embedded", {}).get("details", {}).get("attribute")
            if attr:
                attrs.add(attr)
        return attrs


def href_id(href: str | None) -> str | None:
    """'/api/v3/projects/42' -> '42'."""
    if not href:
        return None
    return href.rstrip("/").rsplit("/", 1)[-1]


class OpenProjectClient:
    """Thin wrapper around the OpenProject REST API (v3)."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/hal+json",
            }
        )
        self._statuses: list[dict] | None = None
        self._project_types: dict[str, list[dict]] = {}
        self._schemas: dict[str, dict] = {}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/api/v3/{path.lstrip('/')}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise OpenProjectError(f"OpenProject nicht erreichbar: {exc}") from exc

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            message = body.get("message") or resp.text[:200] or resp.reason
            raise OpenProjectError(
                f"{method} {path}: {resp.status_code} {message}", resp.status_code, body
            )
        return resp.json() if resp.content else {}

    # Work packages

    def get_work_package(self, key: str | int) -> dict | None:
        """Fetch a work package by numeric or project based id ('EL-5')."""
        try:
            return self._request("GET", f"work_packages/{key}")
        except OpenProjectError as exc:
            if exc.status == 404:
                return None
            raise

    def get_work_packages(self, ids: list[int]) -> dict[int, dict]:
        """Fetch many work packages by numeric id, one request per 100 ids."""
        result = {}
        for i in range(0, len(ids), 100):
            chunk = [str(x) for x in ids[i : i + 100]]
            data = self._request(
                "GET",
                "work_packages",
                params={
                    "filters": json.dumps([{"id": {"operator": "=", "values": chunk}}]),
                    "pageSize": len(chunk),
                },
            )
            for wp in data.get("_embedded", {}).get("elements", []):
                result[wp["id"]] = wp
        return result

    def create_work_package(self, payload: dict) -> dict:
        """Create a work package, project and type are given in payload['_links']."""
        return self._request("POST", "work_packages", json=payload)

    def update_work_package(self, wp_id: int, payload: dict) -> dict:
        """PATCH a work package, payload must contain the current 'lockVersion'."""
        return self._request("PATCH", f"work_packages/{wp_id}", json=payload)

    def create_relation(self, from_id: int, to_id: int, rel_type: str) -> dict:
        """Create a relation ('relates', 'precedes', ...) between two work packages."""
        return self._request(
            "POST",
            f"work_packages/{from_id}/relations",
            json={
                "type": rel_type,
                "_links": {"to": {"href": f"/api/v3/work_packages/{to_id}"}},
            },
        )

    # Lookups (cached per client instance)

    def get_project(self, key: str) -> dict | None:
        """Fetch a project by id or identifier."""
        try:
            return self._request("GET", f"projects/{key}")
        except OpenProjectError as exc:
            if exc.status == 404:
                return None
            raise

    def projects(self) -> list[dict]:
        """All active projects visible to the API user."""
        data = self._request(
            "GET",
            "projects",
            params={
                "filters": json.dumps([{"active": {"operator": "=", "values": ["t"]}}]),
                "pageSize": 500,
            },
        )
        return data.get("_embedded", {}).get("elements", [])

    def project_types(self, project_id: str) -> list[dict]:
        """Work package types enabled in a project."""
        if project_id not in self._project_types:
            data = self._request("GET", f"projects/{project_id}/types")
            self._project_types[project_id] = data.get("_embedded", {}).get(
                "elements", []
            )
        return self._project_types[project_id]

    def statuses(self) -> list[dict]:
        """All work package statuses."""
        if self._statuses is None:
            data = self._request("GET", "statuses")
            self._statuses = data.get("_embedded", {}).get("elements", [])
        return self._statuses

    def status_by_name(self, name: str) -> dict | None:
        """Find a status by (case insensitive) name."""
        name = name.strip().lower()
        return next((s for s in self.statuses() if s["name"].lower() == name), None)

    def schema(self, project_id: str, type_id: str) -> dict:
        """Work package schema for a project/type combination."""
        key = f"{project_id}-{type_id}"
        if key not in self._schemas:
            self._schemas[key] = self._request("GET", f"work_packages/schemas/{key}")
        return self._schemas[key]

    def whoami(self) -> dict[str, Any]:
        """The user the API key belongs to (connection test)."""
        return self._request("GET", "users/me")
