"""Central configuration for the Wanderly backend."""

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_ROOT / ".env"
ENV_EXAMPLE_PATH = BACKEND_ROOT / ".env.example"


def _load_environment() -> None:
    source = ENV_PATH if ENV_PATH.exists() else ENV_EXAMPLE_PATH
    load_dotenv(source, override=False)

def get_api_key(service_name: str) -> str:
    """Get API key for a specific service"""
    return os.getenv(service_name, "")

def load_dotenv_and_get_env() -> None:
    """Load backend environment variables without overriding process values."""
    _load_environment()

def is_service_enabled(service_name: str) -> bool:
    """Check if a service is enabled"""
    return bool(get_api_key(service_name))


_load_environment()
