class ResearchError(Exception):
    """A stable, user-safe error raised by the stock research domain."""

    def __init__(
        self,
        code: str,
        message: str,
        context: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = dict(context or {})
