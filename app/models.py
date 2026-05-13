from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ClueConfig(BaseModel):
    value: int = Field(gt=0)
    prompt: str = Field(min_length=1)
    answer: str = Field(min_length=1)

    @field_validator("prompt", "answer", mode="before")
    @classmethod
    def coerce_text(cls, value: object) -> str:
        return str(value)


class CategoryConfig(BaseModel):
    name: str = Field(min_length=1)
    clues: List[ClueConfig] = Field(min_length=1)


class FinalConfig(BaseModel):
    category: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class GameConfig(BaseModel):
    title: str = Field(min_length=1)
    teams: List[str] = Field(min_length=1)
    categories: List[CategoryConfig] = Field(min_length=1)
    final: FinalConfig

    @model_validator(mode="after")
    def validate_unique_names(self) -> "GameConfig":
        normalized = [team.strip().lower() for team in self.teams]
        if len(set(normalized)) != len(normalized):
            raise ValueError("team names must be unique")
        category_names = [category.name.strip().lower() for category in self.categories]
        if len(set(category_names)) != len(category_names):
            raise ValueError("category names must be unique")
        return self


class TeamState(BaseModel):
    name: str
    score: int = 0
    joined: bool = False
    students: List[str] = Field(default_factory=list)
    answered_students: List[str] = Field(default_factory=list)


class ClueState(BaseModel):
    id: str
    value: int
    prompt: str
    answer: str
    revealed: bool = False
    is_daily_double: bool = False


class CategoryState(BaseModel):
    name: str
    clues: List[ClueState]


class DailyDoubleState(BaseModel):
    clue_id: str
    active: bool = False
    team_name: Optional[str] = None
    wager: Optional[int] = None


class FinalState(BaseModel):
    category: str
    prompt: str
    answer: str
    revealed: bool = False
    wagers: Dict[str, int] = Field(default_factory=dict)
    scored: Dict[str, bool] = Field(default_factory=dict)


class ScoreEvent(BaseModel):
    id: int
    team_name: str
    student_name: Optional[str] = None
    delta: int
    correct: bool


class GameState(BaseModel):
    session_code: str
    title: str
    teams: List[TeamState]
    categories: List[CategoryState]
    final: FinalState
    phase: str = "board"
    current_clue_id: Optional[str] = None
    buzz_winner: Optional[str] = None
    buzz_winner_student: Optional[str] = None
    buzz_expires_at: Optional[float] = None
    last_buzz_team: Optional[str] = None
    last_buzz_student: Optional[str] = None
    buzz_order: List[str] = Field(default_factory=list)
    daily_double: DailyDoubleState
    score_event: Optional[ScoreEvent] = None
    score_event_id: int = 0


class StartGameRequest(BaseModel):
    config_path: str = "games/example.yaml"


class JoinRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str = Field(default="", max_length=80)


class BuzzRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str = Field(default="", max_length=80)


class SelectClueRequest(BaseModel):
    session_code: str
    clue_id: str
    selecting_team: Optional[str] = None


class ScoreClueRequest(BaseModel):
    session_code: str
    correct: bool
    team_name: Optional[str] = None
    wager: Optional[int] = None


class SessionRequest(BaseModel):
    session_code: str


class FinalWagersRequest(BaseModel):
    session_code: str
    wagers: Dict[str, int]


class FinalScoreRequest(BaseModel):
    session_code: str
    results: Dict[str, bool]
