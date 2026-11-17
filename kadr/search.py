"""Поиск: структурный синтаксис + FTS5 + семантика через VectorIndex (FR-5.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .ai.embeddings import embed_query, get_embedding_backend
from .catalog.repository import Catalog
from .models import IndexStatus, SearchHit
from .vector.factory import create_vector_index

_STRUCT = re.compile(r"(?P<field>[A-Za-z_][A-Za-z0-9_.]*)(?P<op>>=|<=|=|>|<|:)(?P<value>\"[^\"]+\"|[^\s]+)")
_DATE_RANGE = re.compile(r"^(\d{4}(?:-\d{2})?(?:-\d{2})?)\.\.(\d{4}(?:-\d{2})?(?:-\d{2})?)$")


@dataclass(slots=True)
class ParsedQuery:
    original: str
    filters: list[tuple[str, str, str]] = field(default_factory=list)
    text: str = ""
    k: int = 50

    @property
    def has_structural(self) -> bool:
        return bool(self.filters)

    @property
    def semantic_text(self) -> str:
        return self.text.strip()


@dataclass(slots=True)
class SearchResult:
    hits: list[SearchHit]
    status: IndexStatus
    parsed: ParsedQuery
    total: int = 0
    text_only: bool = False

    @property
    def index_badge(self) -> str:
        if self.status.status == "active" and self.status.backend in {"sqlite-vec", "ann", "hybrid"}:
            return "семантический поиск: активен"
        if self.status.status == "degraded":
            return f"семантический поиск ограничен — только текстовый поиск ({self.status.reason or 'причина неизвестна'})"
        return f"семантический поиск: {self.status.status}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.parsed.original,
            "text": self.parsed.text,
            "filters": [
                {"field": field_name, "op": op, "value": value}
                for field_name, op, value in self.parsed.filters
            ],
            "hits": [
                {
                    "asset_id": hit.asset_id,
                    "score": round(hit.score, 4),
                    "reason": hit.reason,
                    "fields": hit.fields,
                }
                for hit in self.hits
            ],
            "total": self.total,
            "index_status": self.status.to_dict(),
            "index_badge": self.index_badge,
            "text_only": self.text_only,
        }


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def parse_query(text: str, *, k: int = 50) -> ParsedQuery:
    parsed = ParsedQuery(original=text, k=k)
    spans = []
    for match in _STRUCT.finditer(text or ""):
        field_name = match.group("field").lower()
        op = match.group("op")
        value = _unquote(match.group("value"))
        parsed.filters.append((field_name, op, value))
        spans.append(match.span())
    if spans:
        parts = []
        last = 0
        for start, end in spans:
            parts.append(text[last:start])
            last = end
        parts.append(text[last:])
        parsed.text = " ".join(part.strip() for part in parts if part.strip())
    else:
        parsed.text = (text or "").strip()
    return parsed


def _iso_bound(value: str, *, end: bool = False) -> str | None:
    try:
        if len(value) == 4:
            start = date(int(value), 1, 1)
            end_date = date(int(value), 12, 31)
        elif len(value) == 7:
            year, month = (int(part) for part in value.split("-"))
            start = date(year, month, 1)
            end_date = (date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1))
        elif len(value) == 10:
            start = date.fromisoformat(value)
            end_date = start
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.isoformat()
        return (end_date if end else start).isoformat()
    except ValueError:
        return None


def _fts_query(text: str) -> str:
    terms = [term for term in re.findall(r"[\w\-]{2,}", text, flags=re.UNICODE)]
    # Пользовательский ввод не должен ломать FTS-синтаксис: только quoted terms.
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:16])


def _structural_rows(catalog: Catalog, filters: list[tuple[str, str, str]]) -> set[int] | None:
    clauses: list[str] = []
    params: list[Any] = []
    joins: list[str] = []
    for field_name, op, value in filters:
        if field_name in {"camera", "camera.make", "camera.model"}:
            column = {"camera": ("camera_make", "camera_model"), "camera.make": ("camera_make",), "camera.model": ("camera_model",)}[field_name]
            sub = " OR ".join(f"LOWER(a.{col}) LIKE ?" for col in column)
            clauses.append(f"({sub})")
            params.extend([f"%{value.lower()}%" for _ in column])
            continue
        if field_name in {"date", "taken_at"}:
            if op == ":":
                match = _DATE_RANGE.match(value)
                if match:
                    low, high = _iso_bound(match.group(1)), _iso_bound(match.group(2), end=True)
                    if low and high:
                        clauses.append("a.taken_at >= ? AND a.taken_at <= ?")
                        params.extend([low, high if "T" in high else high + "T23:59:59.999999"])
                        continue
                low = _iso_bound(value)
                if low:
                    high = _iso_bound(value, end=True)
                    clauses.append("a.taken_at >= ? AND a.taken_at <= ?")
                    high_value = high or low
                    params.extend([low, high_value if "T" in high_value else high_value + "T23:59:59.999999"])
                    continue
            low = _iso_bound(value)
            if low:
                operator = {">=": ">=", "<=": "<=", ">": ">", "<": "<", "=": "="}.get(op, "=")
                clauses.append(f"a.taken_at {operator} ?")
                params.append(low)
                continue
            continue
        if field_name == "kind":
            clauses.append("a.kind = ?")
            params.append(value)
        elif field_name in {"ext", "extension"}:
            joins.append("JOIN files f_ext ON f_ext.asset_id=a.id")
            clauses.append("LOWER(f_ext.ext) = ?")
            params.append("." + value.lower().lstrip("."))
        elif field_name in {"rating"}:
            operator = {"=": "=", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}.get(op, "=")
            try:
                clauses.append(f"a.rating {operator} ?")
                params.append(int(value))
            except ValueError:
                continue
        elif field_name in {"class", "media_class"}:
            clauses.append("a.media_class = ?")
            params.append(value)
        elif field_name in {"scene", "scene_type"}:
            clauses.append("a.scene_type = ?")
            params.append(value)
        elif field_name in {"tag", "tags"}:
            alias = f"t_filter_{len(joins)}"
            joins.append(
                f"JOIN asset_tags at_{alias} ON at_{alias}.asset_id=a.id "
                f"JOIN tags {alias} ON {alias}.id=at_{alias}.tag_id"
            )
            clauses.append(f"LOWER({alias}.name) = ?")
            params.append(value.lower())
        elif field_name in {"source", "date.source"}:
            clauses.append("a.taken_at_source = ?")
            params.append(value)
        elif field_name in {"size", "bytes"}:
            operator = {">=": ">=", "<=": "<=", ">": ">", "<": "<", "=": "="}.get(op, "=")
            try:
                clauses.append(f"a.bytes {operator} ?")
                params.append(int(value))
            except ValueError:
                continue
        elif field_name in {"gps", "has_gps"}:
            clauses.append("a.gps_lat IS NOT NULL AND a.gps_lon IS NOT NULL" if value.lower() in {"1", "true", "yes"} else "a.gps_lat IS NULL")
        else:
            # Неизвестное поле не должно молча превращаться в SQL.
            continue
    if not clauses:
        return None
    sql = "SELECT DISTINCT a.id FROM assets a " + " ".join(sorted(set(joins))) + " WHERE " + " AND ".join(f"({c})" for c in clauses)
    try:
        rows = catalog.conn.execute(sql, params).fetchall()
    except Exception:
        return set()
    return {int(row["id"]) for row in rows}


def _fts_ids(catalog: Catalog, text: str, limit: int) -> set[int]:
    query = _fts_query(text)
    if not query:
        return set()
    return set(catalog.fts_search(query, limit=limit))


def _text_terms(text: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[\w\-]{2,}", text or "", flags=re.UNICODE)]


def execute_search(
    catalog: Catalog,
    text: str,
    *,
    k: int = 50,
    backend: str | None = None,
    model_id: str | None = None,
) -> SearchResult:
    parsed = parse_query(text, k=k)
    structural = _structural_rows(catalog, parsed.filters) if parsed.has_structural else None
    fts_ids = _fts_ids(catalog, parsed.text, limit=max(50, k * 5)) if parsed.text else set()

    if model_id is None:
        backend_obj = get_embedding_backend(catalog)
        model_id = backend_obj.model_id
    index = create_vector_index(catalog, backend=backend, model_id=model_id)
    status = index.status()
    vector_count = index.count()

    # Кандидаты: структурные фильтры — жёсткие; FTS — мягкое сужение для гибрида.
    candidates: set[int] | None = structural
    if parsed.text and fts_ids:
        candidates = fts_ids if candidates is None else (candidates & fts_ids)
        if not candidates and structural is None:
            candidates = None  # FTS-сужение пустое, но структурного фильтра нет

    semantic_hits: dict[int, float] = {}
    text_only = False
    if parsed.semantic_text and vector_count > 0:
        query_vector = index_query_vector(parsed.semantic_text)
        try:
            raw_hits = index.search(query_vector, k=max(k * 3, 30), candidate_ids=candidates)
            # cosine similarity ∈ [-1, 1] → [0, 1], не теряя различия отрицательных.
            semantic_hits = {asset_id: (float(score) + 1.0) / 2.0 for asset_id, score in raw_hits}
        except Exception:
            text_only = True
    else:
        text_only = not parsed.semantic_text or vector_count == 0

    # Структурные фильтры — жёсткое ограничение пула. FTS/семантика только
    # скоринг: иначе `camera:Canon` вернёт Nikon из текстового совпадения.
    pool: set[int]
    if structural is not None:
        pool = set(structural)
    elif fts_ids or semantic_hits:
        pool = set(fts_ids) | set(semantic_hits)
    else:
        pool = {int(row["id"]) for row in catalog.list_assets(limit=max(k, 50))}

    if not parsed.text and structural is None:
        pool = {int(row["id"]) for row in catalog.list_assets(limit=k)}

    terms = _text_terms(parsed.text)
    hits: list[SearchHit] = []
    for asset_id in pool:
        asset = catalog.get_asset(asset_id)
        if asset is None:
            continue
        reasons = []
        score = 0.0
        if asset_id in semantic_hits:
            score += semantic_hits[asset_id] * 0.7
            reasons.append("semantic")
        if asset_id in fts_ids:
            score += 0.3
            reasons.append("fts")
        haystack = " ".join(
            str(asset[key] or "")
            for key in ("description", "title", "camera_make", "camera_model", "scene_type", "media_class")
        ).lower()
        tag_names = [row["name"].lower() for row in catalog.tags_for_asset(asset_id)]
        haystack += " " + " ".join(tag_names)
        matched_terms = [term for term in terms if term in haystack]
        if matched_terms:
            score += min(0.4, 0.08 * len(matched_terms))
            reasons.append("text")
        if structural is not None:
            score += 0.1
            reasons.append("filter")
        if not reasons:
            continue
        primary = catalog.primary_file(asset_id)
        hits.append(
            SearchHit(
                asset_id=asset_id,
                score=score,
                reason="+".join(dict.fromkeys(reasons)),
                fields={
                    "taken_at": asset["taken_at"],
                    "kind": asset["kind"],
                    "media_class": asset["media_class"],
                    "scene_type": asset["scene_type"],
                    "camera": f"{asset['camera_make'] or ''} {asset['camera_model'] or ''}".strip(),
                    "tags": tag_names,
                    "path": primary["rel_path"] if primary else None,
                    "date_source": asset["taken_at_source"],
                    "date_confidence": asset["date_confidence"],
                },
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.asset_id))
    total = len(hits)
    return SearchResult(
        hits=hits[:k],
        status=status,
        parsed=parsed,
        total=total,
        text_only=text_only or status.status != "active",
    )


def index_query_vector(text: str) -> list[float]:
    return embed_query(text)


def semantic_status(catalog: Catalog, *, backend: str | None = None) -> dict[str, Any]:
    index = create_vector_index(catalog, backend=backend)
    return index.status().to_dict()


def rebuild_index(catalog: Catalog, *, backend: str | None = None) -> dict[str, Any]:
    index = create_vector_index(catalog, backend=backend)
    count = index.rebuild()
    return {"backend": index.backend, "count": count, "status": index.status().to_dict()}


__all__ = [
    "ParsedQuery",
    "SearchResult",
    "execute_search",
    "parse_query",
    "rebuild_index",
    "semantic_status",
]
