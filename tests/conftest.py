"""
conftest.py — shared fixtures for the ai_lead_bot test suite.
"""
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so that `import i18n`, etc. work.
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import logging as _logging
import types as _types
import unittest.mock as _mock

# ---------------------------------------------------------------------------
# Stub config BEFORE anything else imports it.
# config.py raises ValueError when BOT_TOKEN / GEMINI_API_KEY are absent.
# ---------------------------------------------------------------------------
_fake_config = _types.ModuleType("config")
_fake_config.BOT_TOKEN = "test-token"
_fake_config.GEMINI_API_KEY = "test-gemini-key"
_fake_config.ADMIN_ID = 999
_fake_config.GOOGLE_SHEET_NAME = "Leads"
_fake_config.GEMINI_MODEL = "gemini-3.8-flash"
_fake_config.GEMINI_MODELS = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-flash-latest"]
_fake_config.GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
_fake_config.logger = _logging.getLogger("config")
sys.modules["config"] = _fake_config

# ---------------------------------------------------------------------------
# Stub dotenv
# ---------------------------------------------------------------------------
_dotenv = _types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **kw: None  # type: ignore
sys.modules["dotenv"] = _dotenv

# ---------------------------------------------------------------------------
# Stub Google / gspread packages (not needed in unit tests)
# ---------------------------------------------------------------------------
for _mod_name in (
    "google",
    "google.auth",
    "google.oauth2",
    "google.oauth2.service_account",
    "google.genai",
    "google.genai.types",
    "google.genai.errors",
    "gspread",
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _types.ModuleType(_mod_name)

_genai = sys.modules["google.genai"]
_genai.Client = _mock.MagicMock  # type: ignore
_genai_types = sys.modules["google.genai.types"]
_genai_types.GenerateContentConfig = _mock.MagicMock  # type: ignore
_genai_types.AutomaticFunctionCallingConfig = _mock.MagicMock  # type: ignore
_genai_types.Content = _mock.MagicMock  # type: ignore
_genai_types.Part = _mock.MagicMock  # type: ignore

_genai_errors = sys.modules["google.genai.errors"]
class _APIError(Exception):
    def __init__(self, message="API error", code=500):
        super().__init__(message)
        self.code = code
        self.message = message

_genai_errors.APIError = _APIError  # type: ignore
_genai.errors = _genai_errors  # type: ignore

# ---------------------------------------------------------------------------
# Stub aiogram so handlers can be imported without installing it
# ---------------------------------------------------------------------------

class _PassthroughDecorator:
    """A decorator that returns the decorated function unchanged."""
    def __call__(self, *args, **kwargs):
        import inspect
        if len(args) == 1 and inspect.iscoroutinefunction(args[0]) and not kwargs:
            return args[0]
        def _decorator(func):
            return func
        return _decorator

    def __getattr__(self, name):
        return _PassthroughDecorator()


class _Observer:
    """Stub for aiogram's TelegramEventObserver."""
    def __call__(self, *args, **kwargs):
        import inspect
        if len(args) == 1 and inspect.iscoroutinefunction(args[0]) and not kwargs:
            return args[0]
        def _decorator(func):
            return func
        return _decorator

    def middleware(self, *args, **kwargs):
        pass

    def filter(self, *args, **kwargs):
        pass


class _FakeRouter:
    """Minimal Router / Dispatcher stub: all decorator calls pass through unchanged."""
    def __init__(self, *args, **kwargs):
        self.message = _Observer()
        self.callback_query = _Observer()

    def include_router(self, *a, **kw):
        pass

    def middleware(self, *a, **kw):
        pass


class _FakeF:
    """Stub for aiogram's magic filter F."""
    def __getattr__(self, name):
        return _FakeF()
    def __call__(self, *a, **kw):
        return _FakeF()
    def __eq__(self, other):
        return _FakeF()
    def __ne__(self, other):
        return _FakeF()
    def in_(self, items):
        return _FakeF()
    def startswith(self, prefix):
        return _FakeF()
    def __invert__(self):
        return _FakeF()


_aiogram = _types.ModuleType("aiogram")
_aiogram.Router = _FakeRouter  # type: ignore
_aiogram.Dispatcher = _FakeRouter  # type: ignore
_aiogram.Bot = _mock.MagicMock  # type: ignore
_aiogram.BaseMiddleware = object  # type: ignore
_aiogram.F = _FakeF()  # type: ignore
sys.modules["aiogram"] = _aiogram

class _StubMessage:
    pass

class _StubCallbackQuery:
    pass

_aiogram_types_mod = _types.ModuleType("aiogram.types")
_aiogram_types_mod.Message = _StubMessage  # type: ignore
_aiogram_types_mod.CallbackQuery = _StubCallbackQuery  # type: ignore
_aiogram_types_mod.ReplyKeyboardMarkup = _mock.MagicMock  # type: ignore
_aiogram_types_mod.ReplyKeyboardRemove = _mock.MagicMock  # type: ignore
_aiogram_types_mod.KeyboardButton = _mock.MagicMock  # type: ignore
_aiogram_types_mod.InlineKeyboardMarkup = _mock.MagicMock  # type: ignore
_aiogram_types_mod.InlineKeyboardButton = _mock.MagicMock  # type: ignore
sys.modules["aiogram.types"] = _aiogram_types_mod
_aiogram.types = _aiogram_types_mod  # type: ignore

_aiogram_filters = _types.ModuleType("aiogram.filters")
_aiogram_filters.Command = _PassthroughDecorator()  # type: ignore
_aiogram_filters.CommandStart = _PassthroughDecorator()  # type: ignore
_aiogram_filters.StateFilter = _PassthroughDecorator()  # type: ignore
sys.modules["aiogram.filters"] = _aiogram_filters

_aiogram_fsm = _types.ModuleType("aiogram.fsm")
sys.modules["aiogram.fsm"] = _aiogram_fsm

_aiogram_fsm_ctx = _types.ModuleType("aiogram.fsm.context")
_aiogram_fsm_ctx.FSMContext = object  # type: ignore
sys.modules["aiogram.fsm.context"] = _aiogram_fsm_ctx

_aiogram_fsm_state = _types.ModuleType("aiogram.fsm.state")
_aiogram_fsm_state.StatesGroup = object  # type: ignore
_aiogram_fsm_state.State = lambda: None  # type: ignore
sys.modules["aiogram.fsm.state"] = _aiogram_fsm_state

_aiogram_enums = _types.ModuleType("aiogram.enums")
class _ParseMode:
    HTML = "HTML"
_aiogram_enums.ParseMode = _ParseMode  # type: ignore
sys.modules["aiogram.enums"] = _aiogram_enums

_aiogram_client = _types.ModuleType("aiogram.client")
_aiogram_client_default = _types.ModuleType("aiogram.client.default")
_aiogram_client_default.DefaultBotProperties = _mock.MagicMock  # type: ignore
sys.modules["aiogram.client"] = _aiogram_client
sys.modules["aiogram.client.default"] = _aiogram_client_default


# ---------------------------------------------------------------------------
# Stub services package so `from services.sheets import ...` works cleanly
# ---------------------------------------------------------------------------
_services_pkg = _types.ModuleType("services")
sys.modules["services"] = _services_pkg

_sheets_mod = _types.ModuleType("services.sheets")
_sheets_mod.add_lead_to_sheet = _mock.MagicMock(return_value=True)  # type: ignore
sys.modules["services.sheets"] = _sheets_mod
_services_pkg.sheets = _sheets_mod  # type: ignore

_llm_mod = _types.ModuleType("services.llm")
_llm_mod.get_ai_answer = _mock.AsyncMock(return_value="AI response")  # type: ignore
_llm_mod.clear_history = _mock.MagicMock()  # type: ignore
sys.modules["services.llm"] = _llm_mod
_services_pkg.llm = _llm_mod  # type: ignore

_db_mod_stub = _types.ModuleType("services.db")
_db_mod_stub.init_db = _mock.AsyncMock()  # type: ignore
_db_mod_stub.log_dialog = _mock.AsyncMock()  # type: ignore
_db_mod_stub.get_dialog_stats = _mock.AsyncMock(return_value={"total": 0, "users": 0, "today": 0})  # type: ignore
_db_mod_stub.get_stats = _db_mod_stub.get_dialog_stats
_db_mod_stub.set_user_language = _mock.AsyncMock()  # type: ignore
_db_mod_stub.get_user_language = _mock.AsyncMock(return_value=None)  # type: ignore
sys.modules["services.db"] = _db_mod_stub
_services_pkg.db = _db_mod_stub  # type: ignore


# ---------------------------------------------------------------------------
# Fixture: temporary SQLite database file
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a fresh temporary SQLite file."""
    return str(tmp_path / "test_chat_history.db")
