import asyncio
import contextlib
import logging
import re
from typing import Any

from domains.agent.errors import AgentInvalid
from domains.agent.model import AgentProject, ClaimedRun
from domains.agent.providers.base import ModelProvider, ToolCall
from domains.agent.repo import AgentRepository
from domains.agent.skills import select_skill
from domains.agent.workspace import MAX_FILE_BYTES, AgentWorkspace
from domains.projects.repo import ProjectRepository

MAX_STEPS = 40
MAX_TOOL_CALLS = 120
MAX_OUTPUT_CHARS = 40_000
RUN_TIMEOUT_SECONDS = 600
logger = logging.getLogger("uvicorn.error")

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List editable files in the current application's source workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read one UTF-8 source file by relative path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or replace one UTF-8 source file by relative path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete one source file by relative path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]

SYSTEM_PROMPT = """You're a senior Product Engineer with expertise in UX/UI Design and
Front-end development, you need to work on what the client asks.
You are Surv Agent. Build a complete static website for the user's request.
Use the file tools to create or edit HTML and JavaScript in the workspace.
Create the site's styling with Tailwind CSS utility classes instead of plain handcrafted CSS.
This static workspace has no build step, so include
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
in index.html's head.
Use <style type="text/tailwindcss"> only for small theme or utility extensions when necessary.
The publishable website must have index.html at the workspace root.
Use relative paths for project-owned assets.
You cannot use a shell, install packages, access other projects, or publish the site.
Before finishing, list files and check that index.html references files you created.
After all file work is complete, give the client a concise plain-language summary focused on
the requested business outcome, what visitors can now do, and any meaningful limitation.
Do not show code, file paths, tool names, styling technology, implementation steps,
or developer jargon
in that client-facing response. Never include secrets or hidden reasoning.
"""


def _redact(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/-]+", "Bearer [redacted]", value)
    value = re.sub(r"(?i)(api[_-]?key|password|secret)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return value


def _client_summary(value: str) -> str:
    # Model responses can contain a code sample despite the prompt. Keep it out of the
    # client-facing summary; the separate changed-file review exposes diffs on request.
    without_code = re.sub(r"```.*?```", "", value, flags=re.DOTALL)
    without_code = re.sub(r"`[^`\n]+`", "", without_code)
    without_code = "\n".join(
        line for line in without_code.splitlines()
        if not re.match(r"\s*(?:<[/!a-zA-Z]|(?:import|export|const|let|var|function)\s)", line)
    )
    return _redact(without_code).strip()[:3500]


class AgentWorker:
    def __init__(
        self,
        repo: AgentRepository,
        workspace: AgentWorkspace,
        provider: ModelProvider,
        project_repo: ProjectRepository,
    ) -> None:
        self._repo = repo
        self._workspace = workspace
        self._provider = provider
        self._project_repo = project_repo

    async def _tool(self, project: AgentProject, run: ClaimedRun, call: ToolCall) -> str:
        args = call.arguments
        if call.name == "list_files" and not args:
            names = await asyncio.to_thread(self._workspace.list_files, project.public_id, run.id)
            return "\n".join(names)
        if call.name == "read_file" and isinstance(args.get("path"), str):
            return await asyncio.to_thread(
                self._workspace.read_file, project.public_id, run.id, args["path"]
            )
        if (
            call.name == "write_file"
            and isinstance(args.get("path"), str)
            and isinstance(args.get("content"), str)
        ):
            return await asyncio.to_thread(
                self._workspace.write_file,
                project.public_id,
                run.id,
                args["path"],
                args["content"],
            )
        if call.name == "delete_file" and isinstance(args.get("path"), str):
            return await asyncio.to_thread(
                self._workspace.delete_file, project.public_id, run.id, args["path"]
            )
        raise AgentInvalid("Unknown tool or invalid tool arguments")

    async def _heartbeat(self, run_id: Any) -> None:
        while True:
            await asyncio.sleep(20)
            await self._repo.heartbeat(run_id)

    async def _process(self, run: ClaimedRun) -> None:
        project = await self._repo.get_project(run.project_id, run.created_by)
        if project is None:
            raise AgentInvalid("Project access was revoked")
        if run.provider_id != self._provider.provider_id:
            raise AgentInvalid("Configured provider changed before the run started")
        if run.model_id != self._provider.model_id:
            raise AgentInvalid("Configured model changed before the run started")
        await self._provider.check_ready()
        skill = select_skill(run.skill_id)
        if skill is not None and skill.version != run.skill_version:
            raise AgentInvalid("Selected skill version is unavailable")
        async with self._project_repo.project_lock(run.project_id):
            draft = await self._repo.get_draft(run.project_id, run.created_by)
            await asyncio.to_thread(
                self._workspace.reconcile,
                project.public_id,
                draft.revision if draft else None,
            )
            await asyncio.to_thread(self._workspace.prepare, project.public_id, run.id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        if skill is not None:
            messages.append({"role": "system", "content": skill.guidance})
        messages.append({"role": "user", "content": run.prompt})
        output_count = 0
        tool_count = 0
        final_message = ""
        for _ in range(MAX_STEPS):
            if await self._repo.run_status(run.id) != "running":
                return

            async def on_text(chunk: str) -> None:
                nonlocal output_count
                output_count += len(chunk)
                if output_count > MAX_OUTPUT_CHARS:
                    raise AgentInvalid("Agent output limit reached")

            reply = await self._provider.complete(messages, TOOLS, on_text)
            messages.append(reply.assistant_message)
            if not reply.tool_calls:
                final_message = _client_summary(reply.content)
                break
            for call in reply.tool_calls:
                if await self._repo.run_status(run.id) != "running":
                    return
                tool_count += 1
                if tool_count > MAX_TOOL_CALLS:
                    raise AgentInvalid("Agent tool limit reached")
                try:
                    result = await self._tool(project, run, call)
                    summary = f"{call.name}: {call.arguments.get('path', '')}".strip()
                except AgentInvalid as exc:
                    result = f"Tool error: {exc}"
                    summary = f"{call.name}: rejected"
                await self._repo.add_event(run.id, "tool", _redact(summary))
                messages.append(
                    {
                        "role": "tool", "tool_name": call.name,
                        "tool_call_id": call.id, "content": result[:MAX_FILE_BYTES],
                    }
                )
        else:
            raise AgentInvalid("Agent step limit reached")

        changes = await asyncio.to_thread(self._workspace.changes, project.public_id, run.id)
        for path, kind, diff in changes:
            await self._repo.add_change(run.id, path, kind, _redact(diff) if diff else None)
        if await self._repo.run_status(run.id) != "running":
            return
        await self._repo.add_event(
            run.id,
            "text",
            final_message or "Your draft is ready to review. Preview it before publishing.",
        )
        async with self._project_repo.project_lock(run.project_id):
            if await self._repo.run_status(run.id) != "running":
                return
            revision = await asyncio.to_thread(self._workspace.commit, project.public_id, run.id)
            try:
                committed = await self._repo.complete_draft(run.project_id, run.id, revision)
            except BaseException:
                await asyncio.to_thread(self._workspace.rollback, project.public_id, run.id)
                raise
            if not committed:
                await asyncio.to_thread(self._workspace.rollback, project.public_id, run.id)
                return
            with contextlib.suppress(OSError):
                await asyncio.to_thread(self._workspace.finalize, project.public_id, run.id)
        await self._repo.add_event(run.id, "status", "Draft ready for review")

    async def run_once(self) -> bool:
        run = await self._repo.claim_run()
        if run is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(run.id))
        try:
            async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
                await self._process(run)
        except (Exception, TimeoutError) as exc:
            logger.exception(
                "Surv Agent run %s failed (provider=%s, model=%s) [%s]",
                run.id, run.provider_id, run.model_id, exc,
            )
            # Preserve reviewable diffs from a failed candidate when possible.
            project = await self._repo.get_project(run.project_id, run.created_by)
            if project is not None:
                with contextlib.suppress(Exception):
                    changes = await asyncio.to_thread(
                        self._workspace.changes, project.public_id, run.id
                    )
                    for path, kind, diff in changes:
                        await self._repo.add_change(
                            run.id, path, kind, _redact(diff) if diff else None
                        )
            message = _redact(str(exc))[:500] or "Agent run failed"
            await self._repo.finish(run.id, "failed", message, None)
            await self._repo.add_event(run.id, "error", message)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            with contextlib.suppress(Exception):
                project = await self._repo.get_project(run.project_id, run.created_by)
                if project is not None:
                    await asyncio.to_thread(self._workspace.cleanup, project.public_id, run.id)
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                await self._repo.fail_stale()
                if not await self.run_once():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Surv Agent worker loop failed: %s", exc)
                await asyncio.sleep(3)
