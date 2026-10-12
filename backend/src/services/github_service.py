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
    RepositorySourceFile,
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
        max_source_files: int = 12,
        max_source_file_bytes: int = 12_000,
        max_source_total_bytes: int = 60_000,
    ) -> None:
        self.github_token = github_token if github_token is not None else os.getenv("GITHUB_TOKEN")
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self.max_readme_characters = max_readme_characters
        self.max_tree_entries = max_tree_entries
        self.max_source_files = max_source_files
        self.max_source_file_bytes = max_source_file_bytes
        self.max_source_total_bytes = max_source_total_bytes

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
        readme, readme_path, readme_truncated = await self._fetch_readme(owner, repo, headers)
        file_tree, file_tree_total, file_tree_truncated = await self._fetch_file_tree(
            owner,
            repo,
            repository.get("default_branch", "main"),
            headers,
        )
        source_files, source_files_skipped = await self._fetch_source_files(
            owner,
            repo,
            file_tree,
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
            readme_path=readme_path,
            readme_truncated=readme_truncated,
            file_tree=file_tree,
            file_tree_total=file_tree_total,
            file_tree_truncated=file_tree_truncated,
            source_files=source_files,
            source_files_skipped=source_files_skipped,
            source_files_truncated=source_files_skipped > 0,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoLens",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def _fetch_readme(self, owner: str, repo: str, headers: dict[str, str]) -> tuple[str | None, str, bool]:
        try:
            payload = await self._get_json(f"/repos/{owner}/{repo}/readme", headers)
        except GitHubServiceError as exc:
            if exc.status_code == 404:
                return None, "README.md", False
            raise

        path = payload.get("path") if isinstance(payload.get("path"), str) else "README.md"
        encoded_content = payload.get("content")
        if not isinstance(encoded_content, str):
            return None, path, False

        try:
            decoded = base64.b64decode(encoded_content, validate=False).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return None, path, False

        return decoded[: self.max_readme_characters], path, len(decoded) > self.max_readme_characters

    async def _fetch_file_tree(
        self,
        owner: str,
        repo: str,
        branch: str,
        headers: dict[str, str],
    ) -> tuple[list[RepositoryTreeEntry], int | None, bool]:
        branch_reference = quote(branch, safe="")
        payload = await self._get_json(
            f"/repos/{owner}/{repo}/git/trees/{branch_reference}?recursive=1",
            headers,
        )
        raw_entries = payload.get("tree", [])
        if not isinstance(raw_entries, list):
            raw_entries = []

        entries = [
            RepositoryTreeEntry(
                path=entry["path"],
                entry_type=entry.get("type", "unknown"),
                sha=entry.get("sha"),
                size=entry.get("size"),
            )
            for entry in raw_entries[: self.max_tree_entries]
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        ]
        # GitHub truncates very large trees itself, in which case the real total is unknown.
        github_truncated = bool(payload.get("truncated"))
        total = None if github_truncated else len(raw_entries)
        return entries, total, github_truncated or len(raw_entries) > self.max_tree_entries

    async def _fetch_source_files(
        self,
        owner: str,
        repo: str,
        file_tree: list[RepositoryTreeEntry],
        headers: dict[str, str],
    ) -> tuple[list[RepositorySourceFile], int]:
        candidates = sorted(
            (
                entry
                for entry in file_tree
                if entry.entry_type == "blob"
                and entry.sha
                and entry.size is not None
                and 0 <= entry.size <= self.max_source_file_bytes
                and self._source_file_priority(entry.path) > 0
            ),
            key=lambda entry: (-self._source_file_priority(entry.path), entry.path.count("/"), entry.path),
        )
        selected: list[RepositorySourceFile] = []
        total_bytes = 0

        for entry in candidates:
            if len(selected) >= self.max_source_files or total_bytes >= self.max_source_total_bytes:
                break
            try:
                payload = await self._get_json(
                    f"/repos/{owner}/{repo}/git/blobs/{entry.sha}",
                    headers,
                )
            except GitHubServiceError as exc:
                if exc.status_code == 404:
                    continue
                raise

            encoded_content = payload.get("content")
            if payload.get("encoding") != "base64" or not isinstance(encoded_content, str):
                continue
            try:
                content = base64.b64decode(encoded_content, validate=False).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                continue

            content_bytes = len(content.encode("utf-8"))
            if content_bytes > self.max_source_file_bytes or total_bytes + content_bytes > self.max_source_total_bytes:
                continue
            selected.append(RepositorySourceFile(path=entry.path, content=content))
            total_bytes += content_bytes

        return selected, len(candidates) - len(selected)

    @staticmethod
    def _source_file_priority(path: str) -> int:
        normalized = path.lower()
        parts = normalized.split("/")
        if any(part in {"node_modules", "vendor", "dist", "build", "coverage", ".next", ".venv", "target"} for part in parts):
            return 0
        name = parts[-1]
        if name.endswith((".lock", ".min.js", ".min.css")) or name.endswith((".map", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".pdf")):
            return 0
        if name in {
            "package.json", "pyproject.toml", "requirements.txt", "pom.xml", "build.gradle",
            "build.gradle.kts", "cargo.toml", "go.mod", "composer.json", "gemfile", "makefile", "justfile",
        }:
            return 100
        if name in {"main.py", "app.py", "main.ts", "main.tsx", "index.ts", "index.tsx", "server.ts", "server.js"}:
            return 90
        if any(part in {"routes", "api", "pages"} for part in parts) or name in {"page.tsx", "page.ts", "route.ts", "router.py"}:
            return 80
        if name in {"dockerfile", "docker-compose.yml", "compose.yml", "tsconfig.json", "next.config.js", "next.config.ts"}:
            return 70
        if "test" in parts or "tests" in parts or "spec" in name or "test" in name:
            return 60
        if name.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".cs", ".rb", ".php", ".rs", ".kt", ".swift")):
            return 50 if "src" in parts else 40
        return 0

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
            return GitHubServiceError(
                "Repository not found. RepoLens only works with public GitHub repositories, so check the URL or whether the repository is private.",
                status_code=404,
            )
        if response.status_code == 401:
            # GitHub rejects requests with a bad token even for public repositories.
            return GitHubServiceError(
                "GitHub rejected the server's GITHUB_TOKEN. Replace or remove it in the API's environment settings.",
                status_code=502,
            )
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
