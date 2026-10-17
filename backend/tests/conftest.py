import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path):
    """Test settings: no API key, no .env, fast backoff, low render DPI."""
    return Settings(
        _env_file=None,
        openai_api_key=None,
        ocr_dpi=50,
        ocr_backoff_min_s=0.01,
        ocr_backoff_max_s=0.02,
        data_dir=tmp_path / "data",
    )
