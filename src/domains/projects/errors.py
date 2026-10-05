class InvalidProjectName(ValueError):
    """The project name is blank after trimming whitespace."""


class ProjectNotFound(LookupError):
    """No project exists with the requested ID."""


class OrganizationUnavailable(LookupError):
    """The organization does not exist or is not accessible to the user."""


class ProjectAccessDenied(PermissionError):
    """The user has insufficient priority in the project's organization."""
