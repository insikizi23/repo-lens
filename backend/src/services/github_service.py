from __future__ import annotations

import base64
import binascii
import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from ..schemas.repository_analysis import (
    RepositoryContext,
    RepositoryLanguage,
    RepositoryTreeEntry,
)


class GitHubServiceError(Exception):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class GitHubService:
    """Retrieves a bounded repository context from GitHub's REST API."""

    _repository_url_pattern = re.compile(
        r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/?$"
    )

    def __init__(
        self,
        github_token: str | None = None,
        client: httpx.AsyncClient | None = None,
        max_readme_characters: int = 20_000,
        max_tree_entries: int = 500,
    ) -> None:
        self.github_token = github_token if github_token is not None else os.getenv("GITHUB_TOKEN")
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self.max_readme_characters = max_readme_characters
        self.max_tree_entries = max_tree_entries

    def parse_repository_url(self, repository_url: str) -> tuple[str, str]:
        match = self._repository_url_pattern.match(repository_url.strip())
        if not match:
            raise ValueError("Repository URL must be in the format https://github.com/owner/repository")
        return match.group("owner"), match.group("repo")

    async def fetch_repository_context(self, repository_url: str) -> RepositoryContext:
        owner, repo = self.parse_repository_url(repository_url)
        headers = self._headers()

        repository = await self._get_json(f"/repos/{owner}/{repo}", headers)
        languages = await self._get_json(f"/repos/{owner}/{repo}/languages", headers)
        readme, readme_truncated = await self._fetch_readme(owner, repo, headers)
        file_tree, file_tree_truncated = await self._fetch_file_tree(
            owner,
            repo,
            repository.get("default_branch", "main"),
            headers,
        )

        return RepositoryContext(
            name=repository["name"],
            owner=repository["owner"]["login"],
            description=repository.get("description"),
            stars=repository.get("stargazers_count", 0),
            forks=repository.get("forks_count", 0),
            default_branch=repository.get("default_branch", "main"),
            primary_language=repository.get("language"),
            languages=self._language_breakdown(languages),
            readme=readme,
            readme_truncated=readme_truncated,
            file_tree=file_tree,
            file_tree_truncated=file_tree_truncated,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeAtlas",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def _fetch_readme(self, owner: str, repo: str, headers: dict[str, str]) -> tuple[str | None, bool]:
        try:
            payload = await self._get_json(f"/repos/{owner}/{repo}/readme", headers)
        except GitHubServiceError as exc:
            if exc.status_code == 404:
                return None, False
            raise

        encoded_content = payload.get("content")
        if not isinstance(encoded_content, str):
            return None, False

        try:
            decoded = base64.b64decode(encoded_content, validate=False).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return None, False

        return decoded[: self.max_readme_characters], len(decoded) > self.max_readme_characters

    async def _fetch_file_tree(
        self,
        owner: str,
        repo: str,
        branch: str,
        headers: dict[str, str],
    ) -> tuple[list[RepositoryTreeEntry], bool]:
        branch_reference = quote(branch, safe="")
        payload = await self._get_json(
            f"/repos/{owner}/{repo}/git/trees/{branch_reference}?recursive=1",
            headers,
        )
        raw_entries = payload.get("tree", [])
        if not isinstance(raw_entries, list):
            raw_entries = []

        entries = [
            RepositoryTreeEntry(path=entry["path"], entry_type=entry.get("type", "unknown"))
            for entry in raw_entries[: self.max_tree_entries]
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        ]
        return entries, bool(payload.get("truncated")) or len(raw_entries) > self.max_tree_entries

    async def _get_json(self, path: str, headers: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self.client.get(f"https://api.github.com{path}", headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._translate_status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise GitHubServiceError("Unable to reach GitHub. Please try again.", status_code=502) from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubServiceError("GitHub returned an unexpected response.", status_code=502)
        return payload

    def _translate_status_error(self, exc: httpx.HTTPStatusError) -> GitHubServiceError:
        response = exc.response
        if response.status_code == 404:
            return GitHubServiceError("Repository not found or not publicly accessible.", status_code=404)
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            return GitHubServiceError(
                "GitHub API rate limit reached. Add GITHUB_TOKEN and try again.",
                status_code=429,
            )
        if response.status_code == 403:
            return GitHubServiceError("GitHub denied access to this repository.", status_code=403)
        if response.status_code >= 500:
            return GitHubServiceError("GitHub is temporarily unavailable. Please try again.", status_code=502)
        return GitHubServiceError("GitHub could not retrieve this repository.", status_code=502)

    @staticmethod
    def _language_breakdown(payload: dict[str, Any]) -> list[RepositoryLanguage]:
        language_bytes = {name: value for name, value in payload.items() if isinstance(name, str) and isinstance(value, int)}
        total_bytes = sum(language_bytes.values())
        if total_bytes == 0:
            return []
        return [
            RepositoryLanguage(
                name=name,
                bytes=byte_count,
                percentage=round((byte_count / total_bytes) * 100, 1),
            )
            for name, byte_count in sorted(language_bytes.items(), key=lambda item: item[1], reverse=True)
        ]
