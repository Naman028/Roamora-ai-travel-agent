from __future__ import annotations

from fastapi import HTTPException


class ApiContractException(HTTPException):
    """HTTP failure with a stable public code and retry classification."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.public_message = message
        self.retryable = retryable
