import asyncio
import base64
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from src.main import app
from src.api.routes import analysis as analysis_route
from src.schemas.repository_analysis import (
    RepositoryAnalysis,
    RepositoryContext,
    RepositoryLanguage,
    RepositoryTreeEntry,
)
from src.services.analysis_service import AnalysisService
from src.services.github_service import GitHubService, GitHubServiceError
from src.services.repository_service import RepositoryService


def test_repository_url_validation_rejects_invalid_url() -> None:
    service = RepositoryService()

    with pytest.raises(ValueError):
        service.validate_repository_url("https://example.com/owner/repo")


def test_health_endpoint() -> None:
    async def request_health() -> httpx.Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health")

    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_github_service_creates_bounded_repository_context() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "name": "demo",
                    "owner": {"login": "acme"},
                    "description": "Example repository",
                    "stargazers_count": 42,
                    "forks_count": 7,
                    "default_branch": "main",
                    "language": "Python",
                },
            )
        if request.url.path == "/repos/acme/demo/languages":
            return httpx.Response(200, json={"Python": 700, "TypeScript": 300})
        if request.url.path == "/repos/acme/demo/readme":
            content = base64.b64encode(b"# Demo\nA repository README.").decode()
            return httpx.Response(200, json={"content": content})
        if request.url.path == "/repos/acme/demo/git/trees/main":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "README.md", "type": "blob"},
                        {"path": "src", "type": "tree"},
                        {"path": "src/main.py", "type": "blob"},
                    ]
                },
            )
        return httpx.Response(404)

    async def fetch_context() -> object:
        async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = GitHubService(client=client, max_tree_entries=2, max_readme_characters=10)
            return await service.fetch_repository_context("https://github.com/acme/demo")

    context = asyncio.run(fetch_context())

    assert context.name == "demo"
    assert context.primary_language == "Python"
    assert [language.percentage for language in context.languages] == [70.0, 30.0]
    assert context.readme == "# Demo\nA r"
    assert context.readme_truncated is True
    assert [entry.path for entry in context.file_tree] == ["README.md", "src"]
    assert context.file_tree_truncated is True


def test_github_service_rejects_invalid_repository_url() -> None:
    service = GitHubService()

    with pytest.raises(ValueError):
        service.parse_repository_url("https://example.com/acme/demo")


def test_analysis_service_uses_repository_context_and_validates_model_response() -> None:
    captured_payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "response": """{
                    \"executiveSummary\": \"A FastAPI service with a Next.js client.\",
                    \"technologiesUsed\": [\"Python\", \"TypeScript\"],
                    \"architectureOverview\": \"Frontend calls API routes.\",
                    \"mainComponents\": [{\"name\": \"frontend\", \"description\": \"Next.js client\"}],
                    \"folderResponsibilities\": {\"backend\": \"API and analysis services\"},
                    \"suggestedImprovements\": [\"Add integration tests\"]
                }"""
            },
        )

    context = RepositoryContext(
        name="demo",
        owner="acme",
        description="A demonstration repository",
        stars=42,
        forks=7,
        default_branch="main",
        primary_language="Python",
        languages=[RepositoryLanguage(name="Python", bytes=700, percentage=70.0)],
        readme="# Demo\nActual repository documentation.",
        file_tree=[RepositoryTreeEntry(path="backend/src/main.py", entry_type="blob")],
    )

    async def analyze() -> object:
        async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await AnalysisService(client=client).analyze_repository(context)

    analysis = asyncio.run(analyze())

    assert "acme/demo" in str(captured_payload["prompt"])
    assert "backend/src/main.py" in str(captured_payload["prompt"])
    assert captured_payload["format"] == "json"
    assert analysis.executive_summary == "A FastAPI service with a Next.js client."
    assert analysis.main_components == ["name: frontend; description: Next.js client"]
    assert analysis.folder_responsibilities == ["backend: API and analysis services"]


def test_analysis_service_uses_groq_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_request: dict[str, object] = {}
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_request["url"] = str(request.url)
        captured_request["authorization"] = request.headers["authorization"]
        captured_request["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "executive_summary": "A repository.",
                                    "technologies_used": ["Python"],
                                    "architecture_overview": "A service.",
                                    "main_components": ["backend"],
                                    "folder_responsibilities": ["backend: API"],
                                    "suggested_improvements": ["Add tests"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    context = RepositoryContext(
        name="demo",
        owner="acme",
        description=None,
        stars=0,
        forks=0,
        default_branch="main",
        primary_language="Python",
    )

    async def analyze() -> RepositoryAnalysis:
        async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await AnalysisService(client=client).analyze_repository(context)

    analysis = asyncio.run(analyze())

    assert captured_request["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured_request["authorization"] == "Bearer test-groq-key"
    body = captured_request["body"]
    assert isinstance(body, dict)
    assert body["model"] == "openai/gpt-oss-20b"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 1500
    assert "acme/demo" in body["messages"][0]["content"]
    assert analysis.executive_summary == "A repository."


def test_analyze_route_returns_repository_details_and_analysis() -> None:
    context = RepositoryContext(
        name="demo",
        owner="acme",
        description="A demonstration repository",
        stars=42,
        forks=7,
        default_branch="main",
        primary_language="Python",
        languages=[RepositoryLanguage(name="Python", bytes=700, percentage=70.0)],
    )
    analysis = RepositoryAnalysis(
        executive_summary="A demo repository.",
        technologies_used=["Python"],
        architecture_overview="A small API.",
        main_components=["backend"],
        folder_responsibilities=["backend: API"],
        suggested_improvements=["Add tests"],
    )

    class FakeGitHubService:
        async def fetch_repository_context(self, repository_url: str) -> RepositoryContext:
            assert repository_url == "https://github.com/acme/demo"
            return context

    class FakeAnalysisService:
        async def analyze_repository(self, received_context: RepositoryContext) -> RepositoryAnalysis:
            assert received_context is context
            return analysis

    async def request_analysis() -> httpx.Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/analyze", json={"repository_url": "https://github.com/acme/demo"})

    with patch.object(analysis_route, "GitHubService", FakeGitHubService), patch.object(
        analysis_route, "AnalysisService", FakeAnalysisService
    ):
        response = asyncio.run(request_analysis())

    assert response.status_code == 200
    assert response.json() == {
        "repository": {
            "name": "demo",
            "owner": "acme",
            "description": "A demonstration repository",
            "stars": 42,
            "forks": 7,
            "default_branch": "main",
            "primary_language": "Python",
            "languages": [{"name": "Python", "bytes": 700, "percentage": 70.0}],
        },
        "analysis": analysis.model_dump(),
    }


def test_analyze_route_returns_safe_github_error() -> None:
    class RateLimitedGitHubService:
        async def fetch_repository_context(self, repository_url: str) -> RepositoryContext:
            raise GitHubServiceError("GitHub API rate limit reached. Add GITHUB_TOKEN and try again.", 429)

    async def request_analysis() -> httpx.Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/analyze", json={"repository_url": "https://github.com/acme/demo"})

    with patch.object(analysis_route, "GitHubService", RateLimitedGitHubService):
        response = asyncio.run(request_analysis())

    assert response.status_code == 429
    assert response.json() == {"detail": "GitHub API rate limit reached. Add GITHUB_TOKEN and try again."}
