import asyncio
from uuid import UUID

from domains.agent.errors import AgentAccessDenied, AgentConflict, AgentInvalid, AgentNotFound
from domains.agent.model import AgentChange, AgentEvent, AgentProject, AgentRun
from domains.agent.providers.base import ModelProvider
from domains.agent.repo import AgentRepository
from domains.agent.skills import select_skill
from domains.agent.workspace import AgentWorkspace
from domains.organizations.model import OrganizationRole
from domains.projects.service import ProjectService


class AgentService:
    def __init__(
        self,
        repo: AgentRepository,
        workspace: AgentWorkspace,
        projects: ProjectService,
        provider: ModelProvider,
    ) -> None:
        self._repo = repo
        self._workspace = workspace
        self._projects = projects
        self._provider = provider

    async def project(
        self, project_id: UUID, user_id: UUID, *, mutate: bool = False
    ) -> AgentProject:
        project = await self._repo.get_project(project_id, user_id)
        if project is None:
            raise AgentNotFound("Project not found")
        if mutate and project.role not in (OrganizationRole.ADMIN, OrganizationRole.DEVELOPER):
            raise AgentAccessDenied("Insufficient project access")
        return project

    async def create(
        self, project_id: UUID, user_id: UUID, prompt: str, skill_id: str | None = None
    ) -> AgentRun:
        await self.project(project_id, user_id, mutate=True)
        clean_prompt = prompt.strip()
        if not clean_prompt or len(clean_prompt) > 8000:
            raise AgentInvalid("Prompt must contain 1 to 8000 characters")
        skill = select_skill(skill_id)
        return await self._repo.create_run(
            project_id, user_id, clean_prompt, self._provider.provider_id,
            self._provider.model_id,
            skill.id if skill else None, skill.version if skill else None,
        )

    async def copy_live_app_to_dev(self, project_id: UUID, user_id: UUID) -> None:
        project = await self.project(project_id, user_id, mutate=True)
        if project.status != "DEPLOYED":
            raise AgentConflict("Project has no deployed application")
        if await self._repo.has_active_run(project_id):
            raise AgentConflict("Agent run is active")
        await asyncio.to_thread(self._workspace.copy_live_app_to_dev, project.public_id)

    async def run(self, project_id: UUID, run_id: UUID, user_id: UUID) -> AgentRun:
        await self.project(project_id, user_id)
        run = await self._repo.get_run(project_id, run_id, user_id)
        if run is None:
            raise AgentNotFound("Run not found")
        return run

    async def runs(self, project_id: UUID, user_id: UUID) -> list[AgentRun]:
        await self.project(project_id, user_id)
        return await self._repo.list_runs(project_id, user_id)

    async def cancel(self, project_id: UUID, run_id: UUID, user_id: UUID) -> AgentRun:
        await self.project(project_id, user_id, mutate=True)
        await self.run(project_id, run_id, user_id)
        await self._repo.cancel(run_id)
        result = await self.run(project_id, run_id, user_id)
        return result

    async def events(
        self, project_id: UUID, run_id: UUID, user_id: UUID, after: int
    ) -> list[AgentEvent]:
        await self.run(project_id, run_id, user_id)
        return await self._repo.list_events(project_id, run_id, user_id, after)

    async def changes(
        self, project_id: UUID, run_id: UUID, user_id: UUID
    ) -> list[AgentChange]:
        await self.run(project_id, run_id, user_id)
        return await self._repo.list_changes(project_id, run_id, user_id)

    async def change(
        self, project_id: UUID, run_id: UUID, change_id: UUID, user_id: UUID
    ) -> AgentChange:
        await self.run(project_id, run_id, user_id)
        change = await self._repo.get_change(project_id, run_id, change_id, user_id)
        if change is None:
            raise AgentNotFound("Change not found")
        return change

    async def publish(self, project_id: UUID, user_id: UUID) -> str:
        await self.project(project_id, user_id, mutate=True)
        try:
            return await self._projects.publish_dev_app(
                project_id, user_id, self._repo.has_active_run,
                self._workspace.matches_live, self._workspace.archive,
            )
        except ValueError as exc:
            raise AgentConflict(str(exc)) from exc
