import importlib
import os
from unittest.mock import patch

import pytest


def reload_config(env_vars: dict[str, str]):
    base_env = {
        "BOT_TOKEN": "token123",
        "GEMINI_API_KEY": "key123",
    }
    base_env.update(env_vars)
    with patch.dict(os.environ, base_env, clear=True):
        import config
        return importlib.reload(config)


class TestConfig:
    def test_default_gemini_models_order(self):
        cfg = reload_config({})
        assert cfg.DEFAULT_GEMINI_MODELS == (
            "gemini-2.5-flash",
            "gemini-3.8-flash",
            "gemini-flash-latest",
        )
        assert cfg.GEMINI_MODELS == [
            "gemini-2.5-flash",
            "gemini-3.8-flash",
            "gemini-flash-latest",
        ]
        assert cfg.GEMINI_MODEL == "gemini-2.5-flash"

    def test_custom_gemini_models_env(self):
        cfg = reload_config({"GEMINI_MODELS": "custom-1, custom-2"})
        assert cfg.GEMINI_MODELS == ["custom-1", "custom-2"]
        assert cfg.GEMINI_MODEL == "custom-1"

    def test_single_gemini_model_env_reorders_default(self):
        cfg = reload_config({"GEMINI_MODEL": "gemini-3.8-flash"})
        assert cfg.GEMINI_MODELS == [
            "gemini-3.8-flash",
            "gemini-2.5-flash",
            "gemini-flash-latest",
        ]
        assert cfg.GEMINI_MODEL == "gemini-3.8-flash"

    def test_defaults_for_outbox_and_quota(self):
        cfg = reload_config({})
        assert cfg.OUTBOX_RETRY_INTERVAL_SEC == 300
        assert cfg.OUTBOX_MAX_ATTEMPTS == 5
        assert cfg.DAILY_USER_MESSAGE_LIMIT == 30

    def test_custom_outbox_and_quota(self):
        cfg = reload_config({
            "OUTBOX_RETRY_INTERVAL_SEC": "60",
            "OUTBOX_MAX_ATTEMPTS": "10",
            "DAILY_USER_MESSAGE_LIMIT": "50",
        })
        assert cfg.OUTBOX_RETRY_INTERVAL_SEC == 60
        assert cfg.OUTBOX_MAX_ATTEMPTS == 10
        assert cfg.DAILY_USER_MESSAGE_LIMIT == 50

    @pytest.mark.parametrize("invalid_val", ["0", "-1"])
    def test_invalid_outbox_params_raise(self, invalid_val):
        with pytest.raises(ValueError, match="positive"):
            reload_config({"OUTBOX_RETRY_INTERVAL_SEC": invalid_val})

        with pytest.raises(ValueError, match="positive"):
            reload_config({"OUTBOX_MAX_ATTEMPTS": invalid_val})

    def test_invalid_daily_limit_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            reload_config({"DAILY_USER_MESSAGE_LIMIT": "-1"})

    def test_daily_limit_zero_is_valid(self):
        cfg = reload_config({"DAILY_USER_MESSAGE_LIMIT": "0"})
        assert cfg.DAILY_USER_MESSAGE_LIMIT == 0
