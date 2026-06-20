"""Python client for the Bio 3D Arena ingestion API.

Lets a generator pipeline register its baked 3D outputs without touching the DB:

    from app.client import Bio3DArenaClient
    c = Bio3DArenaClient("http://localhost:8000", admin_token="...")
    c.upsert_category("flowers", "Flowers")
    task = c.create_task("flowers", "Rose bloom", "Generate an open rose.")
    c.upsert_generator("my-rose-gen", "My Rose Generator")
    c.register_output(task["id"], "my-rose-gen", "out.glb", meta={"seed": 7})
    c.recompute()

Only depends on httpx (already a project dependency).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx


class Bio3DArenaClient:
    def __init__(self, base_url: str, admin_token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._admin = {"X-Admin-Token": admin_token} if admin_token else {}
        self._http = httpx.Client(timeout=timeout)

    # ---- read ----------------------------------------------------------------
    def health(self) -> dict:
        return self._get("/healthz")

    def meta(self) -> dict:
        return self._get("/api/meta")

    def list_tasks(self) -> list[dict]:
        return self._get("/api/tasks")["tasks"]

    def leaderboard(self, criterion: str = "overall", category: str = "all") -> list[dict]:
        return self._get("/api/leaderboard", params={"criterion": criterion, "category": category})[
            "rows"
        ]

    # ---- admin / ingestion ---------------------------------------------------
    def upsert_category(self, slug: str, name: str | None = None, description: str = "") -> dict:
        return self._post(
            "/api/categories", json={"slug": slug, "name": name or slug, "description": description}
        )

    def upsert_generator(
        self, slug: str, name: str | None = None, kind: str = "model", description: str = ""
    ) -> dict:
        return self._post(
            "/api/generators",
            json={"slug": slug, "name": name or slug, "kind": kind, "description": description},
        )

    def create_task(self, category: str, title: str, prompt: str, criteria_note: str = "") -> dict:
        return self._post(
            "/api/tasks",
            json={
                "category": category,
                "title": title,
                "prompt": prompt,
                "criteria_note": criteria_note,
            },
        )

    def register_output(
        self,
        task_id: int,
        generator_slug: str,
        glb_path: str | Path,
        title: str = "",
        meta: dict | None = None,
        generator_name: str | None = None,
    ) -> dict:
        path = Path(glb_path)
        data = {
            "task_id": str(task_id),
            "generator_slug": generator_slug,
            "title": title,
            "meta": json.dumps(meta or {}),
        }
        if generator_name:
            data["generator_name"] = generator_name
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "model/gltf-binary")}
            r = self._http.post(
                f"{self.base_url}/api/outputs", data=data, files=files, headers=self._admin
            )
        r.raise_for_status()
        return r.json()

    def recompute(self) -> dict:
        # /admin/recompute takes the token as a form field.
        r = self._http.post(
            f"{self.base_url}/admin/recompute",
            data={"token": self._admin.get("X-Admin-Token", "")},
        )
        r.raise_for_status()
        return r.json()

    # ---- internals -----------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._http.get(f"{self.base_url}{path}", params=params, headers=self._admin)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict) -> dict:  # noqa: A002 — mirror httpx kwarg name
        r = self._http.post(f"{self.base_url}{path}", json=json, headers=self._admin)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._http.close()
