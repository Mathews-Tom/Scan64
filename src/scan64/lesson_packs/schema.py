"""Bound untrusted pack bytes before parsing; never coerce community content."""

from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Any, Literal

import chess
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

MAX_PACK_BYTES = 512 * 1024


def _plain_text(value: str, *, multiline: bool = False) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    if any(
        unicodedata.category(character).startswith("C") and not (multiline and character == "\n")
        for character in value
    ):
        raise ValueError("Text must not contain control characters")
    return value


def _single_line(value: str) -> str:
    return _plain_text(value)


def _multiline(value: str) -> str:
    return _plain_text(value, multiline=True)


def _format_version(value: object) -> object:
    if type(value) is not int:
        raise ValueError("format_version must be the integer 1")
    return value


Text = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_single_line)]
LongText = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_multiline)]
Slug = Annotated[Text, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class PackLessonDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: Slug
    title: Text
    fen: Text
    solution: Annotated[list[Text], Field(min_length=1, max_length=5)]
    objective: LongText
    hints: Annotated[list[LongText], Field(max_length=3)]
    explanation: LongText
    themes: list[Text]

    @model_validator(mode="after")
    def valid_position(self) -> PackLessonDocument:
        if len(self.fen.split()) != 6:
            raise ValueError("FEN must contain all six fields")
        try:
            board = chess.Board(self.fen)
        except ValueError as error:
            raise ValueError(f"Invalid FEN: {error}") from error
        if not board.is_valid():
            raise ValueError("Invalid chess position")
        if board.is_game_over(claim_draw=False):
            raise ValueError("FEN must not be an automatically terminal position")
        if board.halfmove_clock + len(self.solution) >= 100:
            raise ValueError("FEN halfmove clock plus solution length must be below 100")
        return self


class LessonPackDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["scan64-lesson-pack"]
    format_version: Annotated[Literal[1], BeforeValidator(_format_version)]
    id: Slug
    title: Text
    author: Text
    licence: Text
    version: Text
    description: LongText | None = None
    source: Text | None = None
    lessons: Annotated[list[PackLessonDocument], Field(min_length=1, max_length=50)]

    @field_validator("description", "source", mode="before")
    @classmethod
    def supplied_optional_text(cls, value: object) -> object:
        if value is None:
            raise ValueError("Optional text must be a string when supplied")
        return value

    @model_validator(mode="after")
    def unique_lesson_ids(self) -> LessonPackDocument:
        ids = [lesson.id for lesson in self.lessons]
        if len(ids) != len(set(ids)):
            raise ValueError("Repeated lesson id")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Repeated JSON field: {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def parse_pack(raw: bytes) -> LessonPackDocument:
    if len(raw) > MAX_PACK_BYTES:
        raise ValueError(f"Lesson pack exceeds {MAX_PACK_BYTES} bytes")
    # Decode explicitly: json.loads(bytes) otherwise also accepts UTF-16/32.
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Lesson pack must be bounded UTF-8 JSON") from error
    return LessonPackDocument.model_validate(document)
