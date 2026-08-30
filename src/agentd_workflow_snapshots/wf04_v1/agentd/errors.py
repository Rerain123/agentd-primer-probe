"""Typed errors returned by Agent D boundaries."""


class AgentDError(Exception):
    """Base error with a stable machine-readable code."""

    code = "AGENTD_ERROR"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class InputValidationError(AgentDError):
    code = "INVALID_INPUT"


class ToolUnavailableError(AgentDError):
    code = "TOOL_UNAVAILABLE"


class NoCandidateError(AgentDError):
    code = "NO_CANDIDATE"


class SchemaValidationError(AgentDError):
    code = "INVALID_OUTPUT_SCHEMA"


class PersistenceError(AgentDError):
    code = "PERSISTENCE_ERROR"
