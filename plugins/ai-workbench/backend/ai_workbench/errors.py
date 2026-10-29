"""Safe public errors; upstream payloads and credentials are never error text."""

class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class Cancelled(ApiError):
    def __init__(self) -> None:
        super().__init__("CANCELLED", "任务已停止。", 409)
