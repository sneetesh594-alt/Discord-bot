"""Local dashboard defaults; credentials are environment-only."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    public_url: str = "http://127.0.0.1:8765"
    client_id: int | None = None
    client_secret: str = field(default="", repr=False)

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or type(self.port) is not int
            or not 1024 <= self.port <= 65535
        ):
            raise ValueError("Invalid dashboard enablement or port")
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("The local dashboard must bind to a loopback address")
        url = urlsplit(self.public_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or url.port != self.port
        ):
            raise ValueError("DASHBOARD_PUBLIC_URL must be the matching loopback HTTP origin")
        if self.client_id is not None and (type(self.client_id) is not int or self.client_id <= 0):
            raise ValueError("Invalid DISCORD_CLIENT_ID")

    @property
    def origin(self):
        return self.public_url.rstrip("/")

    @property
    def redirect_uri(self):
        return self.origin + "/auth/callback"
