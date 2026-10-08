from dataclasses import dataclass

from domains.agent.errors import AgentInvalid


@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    version: str
    guidance: str


# The runtime receives bounded, reviewed guidance. Skill launchers and scripts are never run
# with tenant files. Updating this text requires a version change for run provenance.
IMPECCABLE = AgentSkill(
    id="impeccable",
    version="surv-static-1",
    guidance=(
        "Impeccable frontend guidance for static sites: understand the visitor's task and "
        "the project's existing visual language before editing. Make a clear hierarchy, "
        "purposeful typography and spacing, responsive layouts, keyboard accessible "
        "controls, useful empty/error states, and readable contrast. Preserve the user's "
        "stated style and factual copy. Verify the resulting HTML/CSS/JS references and "
        "summarize design choices. This guidance does not grant new tools or permissions."
    ),
)


def select_skill(skill_id: str | None) -> AgentSkill | None:
    if skill_id is None:
        return None
    if skill_id == IMPECCABLE.id:
        return IMPECCABLE
    raise AgentInvalid("Unknown or unavailable skill")
