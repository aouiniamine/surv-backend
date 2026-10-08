class AgentNotFound(LookupError):
    pass


class AgentAccessDenied(PermissionError):
    pass


class AgentConflict(RuntimeError):
    pass


class AgentInvalid(ValueError):
    pass
