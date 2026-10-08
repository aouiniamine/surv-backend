from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from domains.agent.errors import AgentConflict
from domains.agent.model import (
    AgentChange,
    AgentDraft,
    AgentEvent,
    AgentProject,
    AgentRun,
    ClaimedRun,
)
from domains.organizations.model import OrganizationRole
from generated.agent.agent import AsyncQuerier, CreateAgentRunParams


def _run(row: object) -> AgentRun:
    return AgentRun(
        id=row.id,
        project_id=row.project_id,
        created_by=row.created_by,
        status=row.status,
        provider_id=row.provider_id,
        model_id=row.model_id,
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        error_message=row.error_message,
        draft_revision=row.draft_revision,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


class AgentRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_project(self, project_id: UUID, user_id: UUID) -> AgentProject | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_agent_project(project_id=project_id, user_id=user_id)
        return (
            AgentProject(row.id, row.public_id, row.status, OrganizationRole(row.role))
            if row is not None
            else None
        )

    async def create_run(
        self, project_id: UUID, user_id: UUID, prompt: str, model_id: str,
        skill_id: str | None, skill_version: str | None,
    ) -> AgentRun:
        try:
            async with self._engine.begin() as conn:
                row = await AsyncQuerier(conn).create_agent_run(
                    CreateAgentRunParams(
                        project_id=project_id, user_id=user_id, prompt=prompt, model_id=model_id,
                        skill_id=skill_id, skill_version=skill_version,
                    )
                )
        except IntegrityError as exc:
            raise AgentConflict("A run is already active for this project") from exc
        if row is None:
            raise RuntimeError("Could not create agent run")
        return _run(row)

    async def get_run(self, project_id: UUID, run_id: UUID, user_id: UUID) -> AgentRun | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_agent_run(
                project_id=project_id, run_id=run_id, user_id=user_id
            )
        return _run(row) if row else None

    async def list_runs(self, project_id: UUID, user_id: UUID) -> list[AgentRun]:
        async with self._engine.connect() as conn:
            return [
                _run(row)
                async for row in AsyncQuerier(conn).list_agent_runs(
                    project_id=project_id, user_id=user_id
                )
            ]

    async def claim_run(self) -> ClaimedRun | None:
        async with self._engine.begin() as conn:
            row = await AsyncQuerier(conn).claim_agent_run()
        return (
            ClaimedRun(
                row.id, row.project_id, row.created_by, row.prompt, row.model_id,
                row.skill_id, row.skill_version,
            )
            if row
            else None
        )

    async def run_status(self, run_id: UUID) -> str | None:
        async with self._engine.connect() as conn:
            return await AsyncQuerier(conn).get_agent_run_status(run_id=run_id)

    async def heartbeat(self, run_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).heartbeat_agent_run(run_id=run_id)

    async def finish(
        self, run_id: UUID, status: str, error_message: str | None, draft_revision: str | None
    ) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).finish_agent_run(
                run_id=run_id,
                status=status,
                error_message=error_message,
                draft_revision=draft_revision,
            )

    async def cancel(self, run_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).cancel_agent_run(run_id=run_id)

    async def fail_stale(self) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).fail_stale_agent_runs()

    async def add_event(self, run_id: UUID, kind: str, content: str) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).add_agent_event(
                run_id=run_id, kind=kind, content=content[:4000]
            )

    async def list_events(
        self, project_id: UUID, run_id: UUID, user_id: UUID, after: int
    ) -> list[AgentEvent]:
        async with self._engine.connect() as conn:
            return [
                AgentEvent(row.sequence, row.kind, row.content, row.created_at)
                async for row in AsyncQuerier(conn).list_agent_events(
                    project_id=project_id,
                    run_id=run_id,
                    user_id=user_id,
                    after_sequence=after,
                )
            ]

    async def add_change(
        self, run_id: UUID, path: str, change_type: str, diff_text: str | None
    ) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).add_agent_change(
                run_id=run_id,
                path=path,
                change_type=change_type,
                diff_text=diff_text,
            )

    async def list_changes(
        self, project_id: UUID, run_id: UUID, user_id: UUID
    ) -> list[AgentChange]:
        async with self._engine.connect() as conn:
            return [
                AgentChange(row.id, row.path, row.change_type, "" if row.has_diff else None)
                async for row in AsyncQuerier(conn).list_agent_changes(
                    project_id=project_id, run_id=run_id, user_id=user_id
                )
            ]

    async def get_change(
        self, project_id: UUID, run_id: UUID, change_id: UUID, user_id: UUID
    ) -> AgentChange | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_agent_change(
                project_id=project_id, run_id=run_id, change_id=change_id, user_id=user_id
            )
        return AgentChange(row.id, row.path, row.change_type, row.diff_text) if row else None

    async def get_draft(self, project_id: UUID, user_id: UUID) -> AgentDraft | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_agent_draft(
                project_id=project_id, user_id=user_id
            )
        return (
            AgentDraft(row.revision, row.source_run_id, row.published_revision, row.created_at)
            if row else None
        )

    async def mark_published(
        self, conn: AsyncConnection, project_id: UUID, revision: str
    ) -> None:
        await AsyncQuerier(conn).mark_agent_draft_published(
            project_id=project_id, revision=revision
        )

    async def set_draft(self, project_id: UUID, run_id: UUID, revision: str) -> None:
        async with self._engine.begin() as conn:
            await AsyncQuerier(conn).upsert_agent_draft(
                project_id=project_id, run_id=run_id, revision=revision
            )

    async def complete_draft(self, project_id: UUID, run_id: UUID, revision: str) -> bool:
        async with self._engine.begin() as conn:
            query = AsyncQuerier(conn)
            if await query.lock_agent_run_status(run_id=run_id) != "running":
                return False
            await query.upsert_agent_draft(project_id=project_id, run_id=run_id, revision=revision)
            await query.finish_agent_run(
                run_id=run_id, status="succeeded", error_message=None, draft_revision=revision
            )
        return True
