"""Configuration from environment variables. See README "Configuration"."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bool(name, default=False):
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "production"))
    session_secret: str = field(default_factory=lambda: os.environ.get("SESSION_SECRET", ""))
    https_only: bool = field(default_factory=lambda: _bool("HTTPS_ONLY", True))
    db_path: str = field(default_factory=lambda: os.environ.get("TYPOLOGY_DB", str(ROOT / "data" / "typology.sqlite3")))
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", ""))
    labeling_config_path: Path = ROOT / "config" / "labeling_config.xml"
    schema_version: str = field(default_factory=lambda: os.environ.get("SCHEMA_VERSION", "1.0"))

    # Label Studio
    ls_url: str = field(default_factory=lambda: os.environ.get("LABEL_STUDIO_URL", "").rstrip("/"))
    ls_api_key: str = field(default_factory=lambda: os.environ.get("LABEL_STUDIO_API_KEY", ""))
    ls_project_id: int = field(default_factory=lambda: int(os.environ.get("LABEL_STUDIO_PROJECT_ID", "0") or 0))

    # Share-link import
    browser_fallback: bool = field(default_factory=lambda: _bool("SHARE_LINK_BROWSER_FALLBACK", False))
    import_rate_per_hour: int = field(default_factory=lambda: int(os.environ.get("IMPORT_RATE_PER_HOUR", "20")))
    pending_import_ttl_hours: int = field(default_factory=lambda: int(os.environ.get("PENDING_IMPORT_TTL_HOURS", "24")))
    # Test-only: send share-link requests to a local fixture server instead of chatgpt.com.
    # Ignored unless APP_ENV=test.
    test_share_origin: str = field(default_factory=lambda: os.environ.get("TEST_SHARE_ORIGIN", ""))

    @property
    def is_test(self):
        return self.env == "test"

    def check(self):
        problems = []
        if len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set (at least 32 characters)")
        if not (self.ls_url and self.ls_api_key and self.ls_project_id):
            problems.append("LABEL_STUDIO_URL, LABEL_STUDIO_API_KEY and LABEL_STUDIO_PROJECT_ID must be set")
        if self.test_share_origin and not self.is_test:
            problems.append("TEST_SHARE_ORIGIN is only allowed with APP_ENV=test")
        if not self.is_test and not self.https_only:
            problems.append("HTTPS_ONLY=0 is only allowed with APP_ENV=test")
        return problems


settings = Settings()
