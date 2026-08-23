from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...schemas.repository_analysis import (
    RepositoryAnalysisResult,
    RepositoryDetails,
)
from ...services.analysis_service import AnalysisService, AnalysisServiceError
from ...services.github_service import GitHubService, GitHubServiceError

router = APIRouter(prefix="/api", tags=["analysis"])


class RepositoryAnalysisRequest(BaseModel):
    repository_url: str = Field(..., min_length=1)


@router.post("/analyze", response_model=RepositoryAnalysisResult)
async def analyze_repository(payload: RepositoryAnalysisRequest) -> RepositoryAnalysisResult:
    github_service = GitHubService()
    analysis_service = AnalysisService()

    try:
        context = await github_service.fetch_repository_context(payload.repository_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitHubServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to fetch repository metadata from GitHub") from exc

    try:
        analysis = await analysis_service.analyze_repository(context)
        return RepositoryAnalysisResult(
            repository=RepositoryDetails(
                name=context.name,
                owner=context.owner,
                description=context.description,
                stars=context.stars,
                forks=context.forks,
                default_branch=context.default_branch,
                primary_language=context.primary_language,
                languages=context.languages,
            ),
            analysis=analysis,
        )
    except AnalysisServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to analyze the repository.") from exc
