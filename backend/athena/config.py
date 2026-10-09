from dataclasses import dataclass
import os
from pathlib import Path
import sys
import sysconfig


def require_free_threading() -> None:
    if sys.version_info[:2] != (3, 14) or not sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise RuntimeError("Athena requires Python 3.14T. Run: uv sync --python 3.14t")
    if sys._is_gil_enabled():
        raise RuntimeError("The GIL was enabled, possibly by an incompatible dependency. Run: uv run --directory backend python -m athena.doctor")


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Athena"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Athena"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "athena"


@dataclass(frozen=True)
class Config:
    data_dir: Path
    repo_dir: Path
    api_key: str = ""
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    max_steps: int = 20
    tool_output_chars: int = 12_000
    cloud_url: str = ""
    public_origin: str = ""
    execution_socket: str = ""
    bind_host: str = "127.0.0.1"
    local_setup_enabled: bool = False

    @property
    def local_setup(self) -> bool:
        return (self.local_setup_enabled and not self.cloud_url and not self.public_origin
                and self.bind_host in ("127.0.0.1", "::1"))

    @classmethod
    def from_env(cls) -> "Config":
        repo = Path(__file__).resolve().parents[2]
        # Editable installs run from the checkout. Only load its ignored .env.
        if repo.name == "backend":
            repo = repo.parent
        from dotenv import load_dotenv
        load_dotenv(repo / ".env")
        return cls(
            data_dir=Path(os.environ.get("ATHENA_DATA_DIR", default_data_dir())).expanduser().resolve(),
            repo_dir=repo,
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            cloud_url=os.environ.get("ATHENA_CLOUD_URL", ""),
            public_origin=os.environ.get("ATHENA_PUBLIC_ORIGIN", ""),
            execution_socket=os.environ.get("ATHENA_EXECUTION_SOCKET", ""),
            bind_host=os.environ.get("ATHENA_BIND_HOST", "127.0.0.1"),
            local_setup_enabled=True,
        )

    @property
    def core_dir(self) -> Path:
        """Protect the entire installed bundle, including the launcher and Python runtime."""
        repo = self.repo_dir.resolve()
        for candidate in (repo, *repo.parents):
            if candidate.suffix == ".app" and (candidate / "Contents" / "Info.plist").is_file():
                return candidate
        return repo

    def prepare(self) -> None:
        data = self.data_dir.resolve()
        repo = self.repo_dir.resolve()
        if data == repo or repo in data.parents:
            raise ValueError("ATHENA_DATA_DIR must be outside the source checkout.")
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in ("artifacts", "workspaces", "browser-profiles", "files", "extensions"):
            (data / directory).mkdir(exist_ok=True, mode=0o700)
