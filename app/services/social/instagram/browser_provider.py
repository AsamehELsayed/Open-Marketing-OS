from .base import unavailable


class BrowserProvider:
    name = "browser"
    needs = ()

    def available(self):
        return self.available_check()

    def available_check(self):
        return False, "Browser review has not been verified"

    def _flag(self):
        return unavailable(
            self.name,
            "Browser review must be explicitly requested and supervised; no automatic browsing",
        )

    def audit_owned(self, username: str = "", *, project_id: str | None = None) -> dict:
        return self._flag()

    def audit_public(self, username: str = "", *, project_id: str | None = None) -> dict:
        return self._flag()
