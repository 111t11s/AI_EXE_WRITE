"""小说章节拆解：报告、技法表、待审核候选和可进化维度。"""

import json
from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text,
                        UniqueConstraint, insert, select, update)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import account_id_type, metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
CATEGORY_LABELS = {"worldbuilding": "世界观构造", "outline": "大纲", "prose": "正文"}
DIMENSION_CATEGORIES = {
    "world": "worldbuilding", "profession": "worldbuilding",
    "character": "outline", "plot": "outline", "prose": "prose",
}
novel_analysis = Table(
    "app_novel_analysis", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("file_names", Text, nullable=False),
    Column("input_chars", Integer, nullable=False, default=0),
    Column("instruction", String(2000), nullable=False, default=""),
    Column("report", LONG_TYPE, nullable=False),
    Column("created_at", DateTime, nullable=False, default=utcnow),
)
analysis_dimension = Table(
    "app_analysis_dimension", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("dimension_key", String(40), nullable=False),
    Column("label", String(40), nullable=False),
    Column("category", String(20), nullable=False, default="outline"),
    Column("description", String(600), nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    UniqueConstraint("account_id", "dimension_key", name="uk_analysis_dimension_account_key"),
)
analysis_candidate = Table(
    "app_analysis_candidate", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("analysis_id", id_type, ForeignKey("app_novel_analysis.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("dimension_key", String(40), nullable=False),
    Column("dimension_label", String(40), nullable=False),
    Column("dimension_reason", String(600), nullable=False, default=""),
    Column("name", String(80), nullable=False),
    Column("summary", String(600), nullable=False),
    Column("mechanism", String(1200), nullable=False, default=""),
    Column("reuse", String(1000), nullable=False, default=""),
    Column("evidence", LONG_TYPE, nullable=False),
    Column("confidence", String(10), nullable=False, default="medium"),
    Column("applicable_genres", Text, nullable=False, default="[]"),
    Column("status", String(10), nullable=False, default="pending"),
    Column("reviewer_id", account_id_type, ForeignKey("app_account.id", ondelete="SET NULL"), nullable=True),
    Column("reviewed_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False, default=utcnow),
)
analysis_technique = Table(
    "app_analysis_technique", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("analysis_id", id_type, ForeignKey("app_novel_analysis.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("candidate_id", id_type, ForeignKey("app_analysis_candidate.id", ondelete="SET NULL"),
           nullable=True),
    Column("dimension_key", String(40), nullable=False),
    Column("dimension_label", String(40), nullable=False),
    Column("name", String(80), nullable=False),
    Column("summary", String(600), nullable=False),
    Column("mechanism", String(1200), nullable=False, default=""),
    Column("reuse", String(1000), nullable=False, default=""),
    Column("evidence", LONG_TYPE, nullable=False),
    Column("confidence", String(10), nullable=False, default="medium"),
    Column("applicable_genres", Text, nullable=False, default="[]"),
    Column("source", String(20), nullable=False, default="standard"),
    Column("created_at", DateTime, nullable=False, default=utcnow),
)


def _load(value):
    try:
        return json.loads(value) if value else None
    except (TypeError, ValueError):
        return None


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _summary(row) -> dict:
    report = _load(row["report"]) or {}
    techniques = [item for dimension in report.get("dimensions") or []
                  for item in dimension.get("techniques") or []]
    available = [item for item in report.get("dimensions") or [] if item.get("available")]
    return {
        "id": row["id"], "file_names": _load(row["file_names"]) or [],
        "input_chars": row["input_chars"], "instruction": row["instruction"],
        "overview": report.get("overview", ""), "technique_count": len(techniques),
        "candidate_count": len(report.get("extra_techniques") or []),
        "dimension_count": len(available), "created_at": _iso(row["created_at"]),
    }


def save_analysis(engine, account_id: int, file_names: list[str], input_chars: int,
                  instruction: str, report: dict, fixed_keys: set[str],
                  dimension_labels: dict[str, str]) -> int:
    with engine.begin() as connection:
        result = connection.execute(insert(novel_analysis).values(
            account_id=account_id,
            file_names=json.dumps(file_names, ensure_ascii=False),
            input_chars=max(0, int(input_chars)),
            instruction=(instruction or "")[:2000],
            report=json.dumps(report, ensure_ascii=False),
            created_at=utcnow()))
        analysis_id = int(result.inserted_primary_key[0])
        for dimension in report.get("dimensions") or []:
            key = (dimension.get("dimension") or "").strip()
            if not key or not dimension.get("available"):
                continue
            label = (dimension.get("label") or dimension_labels.get(key) or key)[:40]
            source = "standard" if key in fixed_keys else "extension"
            for item in dimension.get("techniques") or []:
                _insert_technique(connection, account_id, analysis_id, None, key, label, item, source)
        for item in report.get("extra_techniques") or []:
            key = (item.get("dimension") or "").strip()[:40]
            if not key:
                continue
            connection.execute(insert(analysis_candidate).values(
                account_id=account_id, analysis_id=analysis_id, dimension_key=key,
                dimension_label=(item.get("dimension_label") or key)[:40],
                dimension_reason=(item.get("dimension_reason") or "")[:600],
                name=(item.get("name") or "")[:80], summary=(item.get("summary") or "")[:600],
                mechanism=(item.get("mechanism") or "")[:1200], reuse=(item.get("reuse") or "")[:1000],
                evidence=json.dumps(item.get("evidence") or [], ensure_ascii=False),
                confidence=item.get("confidence") or "medium",
                applicable_genres=json.dumps(item.get("applicable_genres") or [], ensure_ascii=False),
                status="pending", created_at=utcnow()))
        return analysis_id


def _insert_technique(connection, account_id, analysis_id, candidate_id, key, label, item, source):
    connection.execute(insert(analysis_technique).values(
        account_id=account_id, analysis_id=analysis_id, candidate_id=candidate_id,
        dimension_key=key[:40], dimension_label=(label or key)[:40],
        name=(item.get("name") or "")[:80], summary=(item.get("summary") or "")[:600],
        mechanism=(item.get("mechanism") or "")[:1200], reuse=(item.get("reuse") or "")[:1000],
        evidence=json.dumps(item.get("evidence") or [], ensure_ascii=False),
        confidence=item.get("confidence") or "medium",
        applicable_genres=json.dumps(item.get("applicable_genres") or [], ensure_ascii=False),
        source=source, created_at=utcnow()))


def list_reports(engine, account_id: int, limit: int = 20, offset: int = 0) -> dict:
    with engine.begin() as connection:
        rows = connection.execute(select(novel_analysis).where(
            novel_analysis.c.account_id == account_id
        ).order_by(novel_analysis.c.created_at.desc(), novel_analysis.c.id.desc())
        .limit(limit).offset(offset)).mappings().all()
    return {"items": [_summary(row) for row in rows], "limit": limit, "offset": offset}


def get_report(engine, account_id: int, report_id: int) -> dict | None:
    with engine.begin() as connection:
        row = connection.execute(select(novel_analysis).where(
            novel_analysis.c.id == report_id, novel_analysis.c.account_id == account_id
        )).mappings().first()
    if row is None:
        return None
    report = _load(row["report"])
    if report is None:
        return None
    return {**_summary(row), "report": report}


def list_candidates(engine, account_id: int, status: str = "pending") -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(analysis_candidate).where(
            analysis_candidate.c.account_id == account_id,
            analysis_candidate.c.status == status,
        ).order_by(analysis_candidate.c.created_at.desc(), analysis_candidate.c.id.desc())).mappings().all()
    return [{
        "id": row["id"], "analysis_id": row["analysis_id"],
        "dimension": row["dimension_key"], "dimension_label": row["dimension_label"],
        "dimension_reason": row["dimension_reason"], "name": row["name"],
        "summary": row["summary"], "mechanism": row["mechanism"], "reuse": row["reuse"],
        "evidence": _load(row["evidence"]) or [], "confidence": row["confidence"],
        "applicable_genres": _load(row["applicable_genres"]) or [],
        "status": row["status"], "created_at": _iso(row["created_at"]),
    } for row in rows]


def _technique_row(row, category: str = "outline") -> dict:
    return {
        "id": row["id"], "analysis_id": row["analysis_id"],
        "dimension": row["dimension_key"], "dimension_label": row["dimension_label"],
        "category": category, "category_label": CATEGORY_LABELS.get(category, "大纲"),
        "name": row["name"], "summary": row["summary"], "mechanism": row["mechanism"],
        "reuse": row["reuse"], "evidence": _load(row["evidence"]) or [],
        "confidence": row["confidence"], "applicable_genres": _load(row["applicable_genres"]) or [],
        "file_names": [], "source": row["source"],
    }


def list_techniques(engine, account_id: int, limit: int = 500) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(analysis_technique).where(
            analysis_technique.c.account_id == account_id
        ).order_by(analysis_technique.c.created_at.desc(), analysis_technique.c.id.desc())).mappings().all()
        reports = connection.execute(select(novel_analysis).where(
            novel_analysis.c.account_id == account_id
        ).order_by(novel_analysis.c.created_at.desc(), novel_analysis.c.id.desc())).mappings().all()
        categories = _category_map(connection, account_id)
    names = {row["id"]: _load(row["file_names"]) or [] for row in reports}
    items = []
    covered = set()
    for row in rows:
        item = _technique_row(row, categories.get(row["dimension_key"], "outline"))
        item["file_names"] = names.get(row["analysis_id"], [])
        items.append(item)
        covered.add(row["analysis_id"])
    # 兼容升级前的旧报告：没有写入技法表的记录从报告 JSON 回退读取。
    for row in reports:
        if row["id"] in covered:
            continue
        report = _load(row["report"]) or {}
        for dimension in report.get("dimensions") or []:
            if not dimension.get("available"):
                continue
            category = categories.get(dimension.get("dimension"), "outline")
            for item in dimension.get("techniques") or []:
                items.append({
                    "analysis_id": row["id"], "file_names": names.get(row["id"], []),
                    "dimension": item.get("dimension") or dimension.get("dimension"),
                    "dimension_label": dimension.get("label") or dimension.get("dimension"),
                    "category": category, "category_label": CATEGORY_LABELS.get(category, "大纲"),
                    "name": item.get("name", ""), "summary": item.get("summary", ""),
                    "mechanism": item.get("mechanism", ""), "reuse": item.get("reuse", ""),
                    "confidence": item.get("confidence", "medium"),
                    "applicable_genres": item.get("applicable_genres") or [],
                    "evidence": item.get("evidence") or [], "source": "report",
                })
    deduped, seen = [], set()
    for item in items:
        key = (item.get("dimension"), item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:limit]


def list_dimensions(engine, account_id: int) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(analysis_dimension).where(
            analysis_dimension.c.account_id == account_id
        ).order_by(analysis_dimension.c.created_at)).mappings().all()
    return [{"dimension_key": row["dimension_key"], "label": row["label"],
             "category": row["category"] or DIMENSION_CATEGORIES.get(row["dimension_key"], "outline"),
             "description": row["description"]} for row in rows]


def _category_map(connection, account_id: int) -> dict[str, str]:
    rows = connection.execute(select(analysis_dimension).where(
        analysis_dimension.c.account_id == account_id)).mappings().all()
    mapping = dict(DIMENSION_CATEGORIES)
    for row in rows:
        mapping[row["dimension_key"]] = row["category"] or "outline"
    return mapping


def get_techniques(engine, account_id: int, technique_ids: list[int]) -> list[dict]:
    ids = [int(item) for item in technique_ids if int(item) > 0]
    if not ids:
        return []
    with engine.begin() as connection:
        rows = connection.execute(select(analysis_technique).where(
            analysis_technique.c.account_id == account_id,
            analysis_technique.c.id.in_(ids),
        )).mappings().all()
        categories = _category_map(connection, account_id)
        reports = connection.execute(select(novel_analysis).where(
            novel_analysis.c.account_id == account_id,
            novel_analysis.c.id.in_({row["analysis_id"] for row in rows}),
        )).mappings().all()
    names = {row["id"]: _load(row["file_names"]) or [] for row in reports}
    by_id = {row["id"]: row for row in rows}
    result = []
    for technique_id in ids:
        row = by_id.get(technique_id)
        if row is None:
            continue
        category = categories.get(row["dimension_key"], "outline")
        item = _technique_row(row, category)
        item["file_names"] = names.get(row["analysis_id"], [])
        result.append(item)
    return result


def approve_candidate(engine, account_id: int, candidate_id: int, reviewer_id: int,
                      category: str = "outline") -> dict | None:
    with engine.begin() as connection:
        candidate = connection.execute(select(analysis_candidate).where(
            analysis_candidate.c.id == candidate_id,
            analysis_candidate.c.account_id == account_id,
            analysis_candidate.c.status == "pending",
        )).mappings().first()
        if candidate is None:
            return None
        _insert_technique(connection, account_id, candidate["analysis_id"], candidate_id,
                          candidate["dimension_key"], candidate["dimension_label"], {
                              "name": candidate["name"], "summary": candidate["summary"],
                              "mechanism": candidate["mechanism"], "reuse": candidate["reuse"],
                              "evidence": _load(candidate["evidence"]) or [],
                              "confidence": candidate["confidence"],
                              "applicable_genres": _load(candidate["applicable_genres"]) or [],
                          }, "extra")
        connection.execute(update(analysis_candidate).where(
            analysis_candidate.c.id == candidate_id,
            analysis_candidate.c.account_id == account_id,
        ).values(status="approved", reviewer_id=reviewer_id, reviewed_at=utcnow()))
        exists = connection.execute(select(analysis_dimension.c.id).where(
            analysis_dimension.c.account_id == account_id,
            analysis_dimension.c.dimension_key == candidate["dimension_key"],
        )).first()
        if exists:
            connection.execute(update(analysis_dimension).where(
                analysis_dimension.c.account_id == account_id,
                analysis_dimension.c.dimension_key == candidate["dimension_key"],
            ).values(label=candidate["dimension_label"], category=category,
                     description=candidate["dimension_reason"]))
        else:
            connection.execute(insert(analysis_dimension).values(
                account_id=account_id, dimension_key=candidate["dimension_key"],
                label=candidate["dimension_label"], category=category,
                description=candidate["dimension_reason"], created_at=utcnow()))
    return {"candidate_id": candidate_id, "dimension": candidate["dimension_key"],
            "label": candidate["dimension_label"], "category": category}


def reject_candidate(engine, account_id: int, candidate_id: int, reviewer_id: int) -> bool:
    with engine.begin() as connection:
        result = connection.execute(update(analysis_candidate).where(
            analysis_candidate.c.id == candidate_id,
            analysis_candidate.c.account_id == account_id,
            analysis_candidate.c.status == "pending",
        ).values(status="rejected", reviewer_id=reviewer_id, reviewed_at=utcnow()))
        return result.rowcount > 0
