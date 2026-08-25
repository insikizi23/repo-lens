from __future__ import annotations

import re
from typing import Any, List

from pydantic import BaseModel, Field, field_validator, model_validator


class RepositoryMetadata(BaseModel):
    name: str
    owner: str
    description: str | None = None
    default_branch: str
    stars: int
    forks: int
    languages: List[str] = Field(default_factory=list)
    readme: str | None = None


class RepositoryLanguage(BaseModel):
    name: str
    bytes: int
    percentage: float


class RepositoryTreeEntry(BaseModel):
    path: str
    entry_type: str


class RepositoryContext(BaseModel):
    """Bounded, trusted GitHub data supplied to the analysis layer."""

    name: str
    owner: str
    description: str | None = None
    stars: int
    forks: int
    default_branch: str
    primary_language: str | None = None
    languages: List[RepositoryLanguage] = Field(default_factory=list)
    readme: str | None = None
    readme_truncated: bool = False
    file_tree: List[RepositoryTreeEntry] = Field(default_factory=list)
    file_tree_truncated: bool = False


class RepositoryDetails(BaseModel):
    """Repository facts suitable for returning to the dashboard."""

    name: str
    owner: str
    description: str | None = None
    stars: int
    forks: int
    default_branch: str
    primary_language: str | None = None
    languages: List[RepositoryLanguage] = Field(default_factory=list)


class RepositoryAnalysisResult(BaseModel):
    repository: RepositoryDetails
    analysis: "RepositoryAnalysis"


class RepositoryAnalysis(BaseModel):
    executive_summary: str
    technologies_used: List[str] = Field(default_factory=list)
    architecture_overview: str
    main_components: List[str] = Field(default_factory=list)
    folder_responsibilities: List[str] = Field(default_factory=list)
    suggested_improvements: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): item
            for key, item in value.items()
        }

    @field_validator(
        "technologies_used",
        "main_components",
        "folder_responsibilities",
        "suggested_improvements",
        mode="before",
    )
    @classmethod
    def coerce_list_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [f"{key}: {item}" for key, item in value.items()]
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [
                "; ".join(f"{key}: {item}" for key, item in entry.items())
                if isinstance(entry, dict)
                else str(entry)
                for entry in value
            ]
        return value
