class InvalidOrganizationName(ValueError):
    """The organization name is blank or exceeds the stored length."""


class OrganizationNotFound(LookupError):
    """The requested organization does not exist."""


class OrganizationAccessDenied(PermissionError):
    """The user has insufficient priority in the organization."""
