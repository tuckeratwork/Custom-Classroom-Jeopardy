from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ClueConfig(BaseModel):
    value: int = Field(gt=0)
    prompt: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    image: Optional[str] = None

    @field_validator("prompt", "answer", "image", mode="before")
    @classmethod
    def coerce_text(cls, value: object) -> object:
        if value is None:
            return None
        return str(value)


class CategoryConfig(BaseModel):
    name: str = Field(min_length=1)
    clues: List[ClueConfig] = Field(min_length=1)


class FinalConfig(BaseModel):
    category: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class DailyDoubleConfig(BaseModel):
    prompt: str = Field(min_length=1)
    answer: str = Field(min_length=1)

    @field_validator("prompt", "answer", mode="before")
    @classmethod
    def coerce_text(cls, value: object) -> str:
        return str(value)


class GameConfig(BaseModel):
    title: str = Field(min_length=1)
    teams: List[str] = Field(default_factory=list)
    categories: List[CategoryConfig] = Field(min_length=1)
    daily_double: Optional[DailyDoubleConfig] = None
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
    correct_counts: Dict[str, int] = Field(default_factory=dict)


class ClueState(BaseModel):
    id: str
    value: int
    prompt: str
    answer: str
    image: Optional[str] = None
    revealed: bool = False
    is_daily_double: bool = False


class CategoryState(BaseModel):
    name: str
    clues: List[ClueState]


class DailyDoubleState(BaseModel):
    clue_id: str
    active: bool = False
    team_name: Optional[str] = None
    leader: Optional[str] = None
    wager: Optional[int] = None
    wager_submitted_by: Optional[str] = None
    question_revealed: bool = False
    answer_expires_at: Optional[float] = None
    submission: Optional[str] = None
    submitted_by: Optional[str] = None


class FinalState(BaseModel):
    category: str
    prompt: str
    answer: str
    revealed: bool = False
    phase: str = "pending"
    wager_started_at: Optional[float] = None
    wager_expires_at: Optional[float] = None
    answer_started_at: Optional[float] = None
    answer_expires_at: Optional[float] = None
    leaders: Dict[str, str] = Field(default_factory=dict)
    wagers: Dict[str, int] = Field(default_factory=dict)
    wager_submitted_by: Dict[str, str] = Field(default_factory=dict)
    submissions: Dict[str, str] = Field(default_factory=dict)
    submitted_by: Dict[str, str] = Field(default_factory=dict)
    scored: Dict[str, bool] = Field(default_factory=dict)
    final_scores: Dict[str, int] = Field(default_factory=dict)
    reveal_order: List[str] = Field(default_factory=list)
    reveal_step: int = 0
    reveal_spinning_index: Optional[int] = None
    results_revealed: bool = False


class ScoreEvent(BaseModel):
    id: int
    team_name: str
    student_name: Optional[str] = None
    delta: int
    correct: bool
    clue_value: Optional[int] = None
    clue_prompt: Optional[str] = None
    clue_answer: Optional[str] = None


class GameState(BaseModel):
    session_code: str
    title: str
    owner_name: Optional[str] = None
    teams: List[TeamState]
    categories: List[CategoryState]
    final: FinalState
    phase: str = "board"
    current_clue_id: Optional[str] = None
    last_completed_clue_id: Optional[str] = None
    buzz_winner: Optional[str] = None
    buzz_winner_student: Optional[str] = None
    buzz_expires_at: Optional[float] = None
    auto_random_enabled: bool = False
    auto_random_expires_at: Optional[float] = None
    reactions_enabled: bool = True
    last_buzz_team: Optional[str] = None
    last_buzz_student: Optional[str] = None
    buzz_order: List[str] = Field(default_factory=list)
    steal_eligible: bool = False
    steal_active: bool = False
    steal_lockout_team: Optional[str] = None
    daily_double: DailyDoubleState
    score_event: Optional[ScoreEvent] = None
    score_event_id: int = 0


class StartGameRequest(BaseModel):
    config_path: str = "games/example.yaml"
    previous_session_code: Optional[str] = None
    team_names: Optional[List[str]] = None


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


class AutoRandomRequest(BaseModel):
    session_code: str
    enabled: bool


class ReactionsToggleRequest(BaseModel):
    session_code: str
    enabled: bool


class ReactionRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str = Field(max_length=80)
    emoji: str = Field(min_length=1, max_length=8)


class StudentRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str = Field(max_length=80)


class ScoreOverrideRequest(BaseModel):
    session_code: str
    scores: Dict[str, int]


class FinalWagersRequest(BaseModel):
    session_code: str
    wagers: Dict[str, int]


class FinalScoreRequest(BaseModel):
    session_code: str
    results: Dict[str, bool]


class FinalWagerRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str
    wager: int = Field(ge=0)


class FinalAnswerRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str
    answer: str


class DailyDoubleWagerRequest(BaseModel):
    session_code: str
    team_name: str
    wager: int = Field(ge=0)


class DailyDoubleStudentWagerRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str
    wager: int = Field(ge=0)


class DailyDoubleAnswerRequest(BaseModel):
    session_code: str
    team_name: str
    student_name: str
    answer: str
