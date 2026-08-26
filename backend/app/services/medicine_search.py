from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

MatchField = Literal["barcode", "sku", "name", "genericName", "alias", "strength", "dosageForm"]
MatchQuality = Literal["exact", "partial", "fuzzy", "supporting"]

_UNITS = r"(?:mcg|mg|gm|g|ml|l|iu|units?|meq|mmol|%)"
_UNIT_RE = re.compile(rf"\b\d+(?:\.\d+)?\s*{_UNITS}\b", re.IGNORECASE)
_SEPARATORS_RE = re.compile(r"[^\w+/%.-]+", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")
_UNIT_SPACE_RE = re.compile(rf"(\d)\s+({_UNITS[3:-1]})(?=\b|/)", re.IGNORECASE)
#: The digits inside a strength value, so "500" in a query can be checked
#: against "500 mg" without the unit having to be typed.
_STRENGTH_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class MedicineMatch:
    field: MatchField
    quality: MatchQuality
    text: str
    score: float
    rank: int


def normalize_medicine_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.translate(str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}))
    normalized = _SPACES_RE.sub(" ", _SEPARATORS_RE.sub(" ", normalized)).strip()
    return _UNIT_SPACE_RE.sub(r"\1\2", normalized)


def medicine_edit_distance(left_raw: str, right_raw: str) -> int:
    left = list(normalize_medicine_text(left_raw))
    right = list(normalize_medicine_text(right_raw))
    matrix = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for index in range(len(left) + 1):
        matrix[index][0] = index
    for index in range(len(right) + 1):
        matrix[0][index] = index
    for row in range(1, len(left) + 1):
        for column in range(1, len(right) + 1):
            substitution = 0 if left[row - 1] == right[column - 1] else 1
            distance = min(
                matrix[row - 1][column] + 1,
                matrix[row][column - 1] + 1,
                matrix[row - 1][column - 1] + substitution,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                distance = min(distance, matrix[row - 2][column - 2] + 1)
            matrix[row][column] = distance
    return matrix[-1][-1]


def _edit_budget(length: int) -> int:
    if length < 3:
        return 0
    if length <= 5:
        return 1
    if length <= 9:
        return 2
    return 3


def _fuzzy_score(query: str, field: str) -> float | None:
    if len(query) < 3:
        return None
    words = field.split()
    width = max(1, len(query.split()))
    candidates = {field, *(" ".join(words[index : index + width]) for index in range(len(words)))}
    accepted: list[float] = []
    for candidate in candidates:
        if not candidate:
            continue
        distance = medicine_edit_distance(query, candidate)
        score = 1 - distance / max(len(query), len(candidate))
        if distance <= _edit_budget(len(query)) and score >= 0.70:
            accepted.append(score)
    return max(accepted) if accepted else None


def _remove_phrase(value: str, phrase: str) -> str:
    if not phrase:
        return value
    removed = re.sub(rf"(^|\s){re.escape(phrase)}(?=\s|$)", " ", value)
    return _SPACES_RE.sub(" ", removed).strip()


def _bare_number_terms(value: str) -> list[str]:
    """Standalone number tokens: "500" in "napa 500", but not in "b12"."""
    return [token for token in value.split(" ") if _BARE_NUMBER_TOKEN_RE.fullmatch(token)]


_BARE_NUMBER_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")


def search_core(raw_query: str, dosage_forms: list[tuple[object, str]]) -> tuple[str, object | None, tuple[str, ...]]:
    """Split globally recognizable dosage/strength support terms from medicine text."""
    query = normalize_medicine_text(raw_query)
    matching_forms = [
        (form_id, normalize_medicine_text(name))
        for form_id, name in dosage_forms
        if normalize_medicine_text(name) and f" {normalize_medicine_text(name)} " in f" {query} "
    ]
    matching_forms.sort(key=lambda row: len(row[1]), reverse=True)
    dosage_id: object | None = None
    if matching_forms:
        dosage_id, dosage_text = matching_forms[0]
        query = _remove_phrase(query, dosage_text)
    strengths = tuple(normalize_medicine_text(match.group(0)) for match in _UNIT_RE.finditer(query))
    for strength in strengths:
        query = _remove_phrase(query, strength)
    return query, dosage_id, strengths


def match_medicine_text(
    *,
    name: str,
    generic_name: str | None,
    strength: str | None,
    dosage_form: str | None,
    raw_query: str,
) -> MedicineMatch | None:
    query = normalize_medicine_text(raw_query)
    if not query:
        return None
    normalized_name = normalize_medicine_text(name)
    normalized_generic = normalize_medicine_text(generic_name or "")
    if normalized_name == query:
        return MedicineMatch("name", "exact", name, 1, 2)
    if normalized_generic and normalized_generic == query:
        return MedicineMatch("genericName", "exact", generic_name or "", 1, 3)

    remaining = query
    support: list[MedicineMatch] = []
    normalized_dosage = normalize_medicine_text(dosage_form or "")
    if normalized_dosage and f" {normalized_dosage} " in f" {remaining} ":
        remaining = _remove_phrase(remaining, normalized_dosage)
        support.append(MedicineMatch("dosageForm", "supporting", dosage_form or "", 1, 10))
    requested_strengths = tuple(normalize_medicine_text(match.group(0)) for match in _UNIT_RE.finditer(query))
    normalized_strength = normalize_medicine_text(strength or "")
    if requested_strengths:
        if not normalized_strength or any(term not in normalized_strength for term in requested_strengths):
            return None
        for term in requested_strengths:
            remaining = _remove_phrase(remaining, term)
        support.append(MedicineMatch("strength", "supporting", strength or "", 1, 10))
    if normalized_strength:
        # A bare "500" ("napa 500 tablet") is a strength the cashier left the unit
        # off of. It matches only the digits written on the row: 500 does not stand
        # in for 650, and a row with no strength at all keeps the number for its
        # name to answer.
        strength_numbers = frozenset(_STRENGTH_NUMBER_RE.findall(normalized_strength))
        for number in _bare_number_terms(remaining):
            if number not in strength_numbers:
                return None
            remaining = _remove_phrase(remaining, number)
            support.append(MedicineMatch("strength", "supporting", strength or "", 1, 10))
    if not remaining:
        return support[0] if support else None
    if normalized_name == remaining:
        return MedicineMatch("name", "exact", name, 1, 2)
    if normalized_generic and normalized_generic == remaining:
        return MedicineMatch("genericName", "exact", generic_name or "", 1, 3)
    if remaining in normalized_name:
        return MedicineMatch("name", "partial", name, len(remaining) / len(normalized_name), 5)
    if normalized_generic and remaining in normalized_generic:
        return MedicineMatch("genericName", "partial", generic_name or "", len(remaining) / len(normalized_generic), 6)
    name_score = _fuzzy_score(remaining, normalized_name)
    generic_score = _fuzzy_score(remaining, normalized_generic) if normalized_generic else None
    if name_score is not None:
        return MedicineMatch("name", "fuzzy", name, name_score, 8)
    if generic_score is not None:
        return MedicineMatch("genericName", "fuzzy", generic_name or "", generic_score, 9)
    return None


def best_match(*matches: MedicineMatch | None) -> MedicineMatch | None:
    available = [match for match in matches if match is not None]
    return min(available, key=lambda match: (match.rank, -match.score)) if available else None
