from __future__ import annotations

import re
from typing import Any

import httpx

from ..schemas.repository_analysis import RepositoryMetadata


class RepositoryService:
    def __init__(self, github_token: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.github_token = github_token
        self.client = client or httpx.AsyncClient(timeout=10.0)

    def validate_repository_url(self, repository_url: str) -> str:
        pattern = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
        match = pattern.match(repository_url.strip())
        if not match:
            raise ValueError("Repository URL must be in the format https://github.com/owner/repository")
        return repository_url.strip()

    async def fetch_repository_metadata(self, repository_url: str) -> RepositoryMetadata:
        normalized_url = self.validate_repository_url(repository_url)
        owner, repo = self._parse_owner_and_repo(normalized_url)

        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"

        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        response = await self.client.get(api_url, headers=headers)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        languages_url = payload.get("languages_url")
        languages_payload: dict[str, Any] = {}
        if languages_url:
            languages_response = await self.client.get(languages_url, headers=headers)
            languages_response.raise_for_status()
            languages_payload = languages_response.json()

        readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        readme_payload: dict[str, Any] | None = None
        try:
            readme_response = await self.client.get(readme_url, headers=headers)
            readme_response.raise_for_status()
            readme_payload = readme_response.json()
        except httpx.HTTPStatusError:
            readme_payload = None

        readme_text = None
        if readme_payload and readme_payload.get("content"):
            import base64

            readme_text = base64.b64decode(readme_payload["content"]).decode("utf-8", errors="ignore")

        return RepositoryMetadata(
            name=payload["name"],
            owner=payload["owner"]["login"],
            description=payload.get("description"),
            default_branch=payload.get("default_branch", "main"),
            stars=payload.get("stargazers_count", 0),
            forks=payload.get("forks_count", 0),
            languages=list(languages_payload.keys()),
            readme=readme_text,
        )

    def _parse_owner_and_repo(self, repository_url: str) -> tuple[str, str]:
        parts = repository_url.rstrip("/").split("/")
        return parts[-2], parts[-1]
