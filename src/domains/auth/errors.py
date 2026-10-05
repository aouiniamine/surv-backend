class AccountExists(ValueError):
    """An account already uses this email address."""


class AccountNotFound(LookupError):
    """No account exists for this email address."""


class InvalidOtp(ValueError):
    """The code is wrong, expired, or already used."""


class RegistrationNotValidated(ValueError):
    """The registration OTP has not been validated or has expired."""


class InvalidProfileFields(ValueError):
    """One of the profile or organization fields is blank."""


class Unauthorized(ValueError):
    """The bearer token is missing or invalid."""
