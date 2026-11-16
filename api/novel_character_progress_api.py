"""Read-only access to facts automatically saved with chapter drafts."""

from typing import Annotated

from fastapi import APIRouter, Query

from api.account_api import CurrentAccount
from api.character_progress import read_progress
from api.database import Database, transaction
from api.novel_schemas import RowId, Sequence
from api.ownership import require_readable
from api.writing_schemas import CharacterProgressPage


router = APIRouter(prefix="/writing", tags=["正文人物档案"])


@router.get("/projects/{project_id}/character-progress", response_model=CharacterProgressPage)
def list_progress(project_id: RowId, user: CurrentAccount, engine: Database,
                  before_chapter_number: Sequence | None = None, latest: bool = False,
                  limit: Annotated[int, Query(ge=1, le=100)] = 100,
                  offset: Annotated[int, Query(ge=0)] = 0):
    with transaction(engine) as connection:
        require_readable(connection, "novel_project", project_id, user)
        return read_progress(connection, project_id, before_chapter_number=before_chapter_number,
                             latest=latest, limit=limit, offset=offset)
