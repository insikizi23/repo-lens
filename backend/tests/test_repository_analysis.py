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
    Recommendation,
    RepositoryAnalysis,
    RepositoryContext,
    RepositoryFacts,
    RepositoryLanguage,
    RepositorySourceFile,
    RepositoryTreeEntry,
)
from src.services.analysis_service import AnalysisService, AnalysisServiceError
from src.services.facts_service import build_repository_facts
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


def test_github_service_fetches_a_bounded_sample_of_source_files() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "name": "demo",
                    "owner": {"login": "acme"},
                    "default_branch": "main",
                },
            )
        if request.url.path == "/repos/acme/demo/languages":
            return httpx.Response(200, json={"Python": 100})
        if request.url.path == "/repos/acme/demo/readme":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/repos/acme/demo/git/trees/main":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "package.json", "type": "blob", "sha": "manifest", "size": 14},
                        {"path": "src/main.py", "type": "blob", "sha": "entrypoint", "size": 12},
                        {"path": "node_modules/lib/index.js", "type": "blob", "sha": "vendor", "size": 10},
                    ]
                },
            )
        if request.url.path == "/repos/acme/demo/git/blobs/manifest":
            content = base64.b64encode(b'{"scripts":{}}').decode()
            return httpx.Response(200, json={"encoding": "base64", "content": content})
        if request.url.path == "/repos/acme/demo/git/blobs/entrypoint":
            content = base64.b64encode(b"def main():\n").decode()
            return httpx.Response(200, json={"encoding": "base64", "content": content})
        return httpx.Response(404)

    async def fetch_context() -> RepositoryContext:
        async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = GitHubService(client=client, max_source_files=1)
            return await service.fetch_repository_context("https://github.com/acme/demo")

    context = asyncio.run(fetch_context())

    assert [source.path for source in context.source_files] == ["package.json"]
    assert context.source_files[0].content == '{"scripts":{}}'
    assert context.source_files_truncated is True


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
                    \"suggestedImprovements\": [\"Add integration tests\"],
                    \"firstSteps\": [\"Run pytest.\"],
                    \"evidence\": [{
                        \"claim\": \"The entry point is named main.\",
                        \"path\": \"backend/src/main.py\",
                        \"quote\": \"def main():\"
                    }]
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
        source_files=[RepositorySourceFile(path="backend/src/main.py", content="def main():\n    pass\n")],
    )

    async def analyze() -> object:
        async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await AnalysisService(client=client).analyze_repository(context)

    analysis = asyncio.run(analyze())

    assert "acme/demo" in str(captured_payload["prompt"])
    assert "backend/src/main.py" in str(captured_payload["prompt"])
    assert "def main():" in str(captured_payload["prompt"])
    assert captured_payload["format"] == "json"
    assert analysis.executive_summary == "A FastAPI service with a Next.js client."
    assert analysis.main_components == ["name: frontend; description: Next.js client"]
    assert analysis.folder_responsibilities == ["backend: API and analysis services"]
    assert analysis.first_steps == ["Run pytest."]
    assert analysis.evidence[0].line_start == 1
    assert analysis.evidence[0].line_end == 1
    assert analysis.evidence[0].quote == "def main():"


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
                                    "recommendations": ["Add tests"],
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
    assert body["max_tokens"] == 2500
    assert "acme/demo" in body["messages"][0]["content"]
    assert analysis.executive_summary == "A repository."
    assert analysis.recommendations[0].suggestion == "Add tests"
    assert analysis.recommendations[0].verified is False


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
        recommendations=[Recommendation(suggestion="Add tests")],
    )

    class FakeGitHubService:
        async def fetch_repository_context(self, repository_url: str) -> RepositoryContext:
            assert repository_url == "https://github.com/acme/demo"
            return context

    class FakeAnalysisService:
        async def analyze_repository(
            self, received_context: RepositoryContext, facts: RepositoryFacts | None = None
        ) -> RepositoryAnalysis:
            assert received_context is context
            assert facts is not None
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
    body = response.json()
    assert body["facts"]["coverage"]["readme_found"] is False
    del body["facts"]
    assert body == {
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


def test_facts_extracts_commands_and_entry_points_from_files() -> None:
    package_json = json.dumps(
        {"main": "server.js", "scripts": {"dev": "next dev", "test": "jest", "lint": "eslint .", "prebuild": "x"}},
        indent=2,
    )
    readme = "# Demo\n\n```bash\ngit clone https://github.com/acme/demo\n$ npm run dev\n```\n\n```js\nnpm run fake\n```\n"
    context = RepositoryContext(
        name="demo",
        owner="acme",
        stars=0,
        forks=0,
        default_branch="main",
        readme=readme,
        file_tree=[
            RepositoryTreeEntry(path="web/package.json", entry_type="blob"),
            RepositoryTreeEntry(path="web/pnpm-lock.yaml", entry_type="blob"),
            RepositoryTreeEntry(path="web/server.js", entry_type="blob"),
            RepositoryTreeEntry(path="api/main.py", entry_type="blob"),
            RepositoryTreeEntry(path="node_modules/x/main.py", entry_type="blob"),
            RepositoryTreeEntry(path="Makefile", entry_type="blob"),
        ],
        source_files=[
            RepositorySourceFile(path="web/package.json", content=package_json),
            RepositorySourceFile(path="Makefile", content=".PHONY: test\ntest:\n\tpytest\nVAR := 1\n"),
        ],
    )

    facts = build_repository_facts(context)
    commands = {(command.command, command.working_directory, command.kind) for command in facts.commands}

    assert ("pnpm install", "web", "setup") in commands
    assert ("pnpm dev", "web", "run") in commands
    assert ("pnpm test", "web", "test") in commands
    assert ("pnpm lint", "web", "quality") in commands
    assert ("make test", ".", "test") in commands
    assert ("npm run dev", ".", "run") in commands
    assert ("git clone https://github.com/acme/demo", ".", "setup") in commands
    assert not any("prebuild" in command or "fake" in command or "VAR" in command for command, _, _ in commands)
    dev = next(command for command in facts.commands if command.command == "pnpm dev")
    assert dev.source_path == "web/package.json" and dev.line == 4

    entry_points = {entry.path for entry in facts.entry_points}
    assert entry_points == {"web/server.js", "api/main.py"}
    assert facts.coverage.source_files_inspected == ["web/package.json", "Makefile"]


def test_analysis_verification_flags_unsupported_recommendations_and_paths() -> None:
    context = RepositoryContext(
        name="demo",
        owner="acme",
        stars=0,
        forks=0,
        default_branch="main",
        readme="# Demo\nRun it with care.",
        file_tree=[RepositoryTreeEntry(path="src/app.py", entry_type="blob")],
        source_files=[RepositorySourceFile(path="src/app.py", content="import os\nSECRET = 'hardcoded'\n")],
    )
    analysis = RepositoryAnalysis.model_validate(
        {
            "executiveSummary": "Demo.",
            "architectureOverview": ["One", "module."],
            "readingOrder": ["src/app.py", {"path": "./src/missing.py", "reason": "made up"}],
            "recommendations": [
                {"suggestion": "Load SECRET from env", "rationale": "Hardcoded secret", "path": "src/app.py", "quote": "SECRET = 'hardcoded'"},
                {"suggestion": "Document setup", "path": "README.md", "quote": "Run it with care."},
                {"suggestion": "Add CI", "path": "src/app.py", "quote": "not in the file"},
            ],
        }
    )

    verified = AnalysisService._verify_analysis(analysis, context)

    assert verified.architecture_overview == "One module."
    assert [(step.path, step.verified) for step in verified.reading_order] == [("src/app.py", True)]
    first, readme, unsupported = verified.recommendations
    assert (first.verified, first.line_start, first.line_end) == (True, 2, 2)
    assert (readme.verified, readme.line_start) == (True, 2)
    assert unsupported.verified is False and unsupported.quote == ""


def test_prompt_stays_within_budget_and_names_omitted_files() -> None:
    context = RepositoryContext(
        name="demo",
        owner="acme",
        stars=0,
        forks=0,
        default_branch="main",
        readme="intro line\n" * 2000,
        file_tree=[RepositoryTreeEntry(path=f"src/module_{index}.py", entry_type="blob") for index in range(400)],
        source_files=[RepositorySourceFile(path=f"src/module_{index}.py", content="x = 1\n" * 800) for index in range(6)],
    )
    service = AnalysisService()
    prompt = service._build_prompt(context, build_repository_facts(context))

    # Budgeted sections plus fixed instructions stay well under the provider's request ceiling.
    assert len(prompt) < service.max_prompt_chars + 6000
    assert "README was truncated" in prompt
    assert "File tree was truncated" in prompt
    assert "src/module_5.py" in prompt.split("--- SOURCE FILES END ---")[1]


def test_groq_request_too_large_returns_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            413,
            json={"error": {"message": "Request too large for model in organization org_abc123 on tokens per minute (TPM): Limit 8000"}},
        )

    context = RepositoryContext(name="demo", owner="acme", stars=0, forks=0, default_branch="main")

    async def analyze() -> RepositoryAnalysis:
        async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await AnalysisService(client=client).analyze_repository(context)

    with pytest.raises(AnalysisServiceError) as caught:
        asyncio.run(analyze())

    assert caught.value.status_code == 413
    assert "too large" in caught.value.detail
    assert "org_abc123" not in caught.value.detail
