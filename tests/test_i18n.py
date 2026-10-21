import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from i18n import LANG_SELECT_MSG, TEXTS, get_lang, t


class TestGetLang:
    def test_uk_code_returns_uk(self):
        assert get_lang("uk") == "uk"

    def test_uk_UA_returns_uk(self):
        assert get_lang("uk-UA") == "uk"

    def test_uk_prefix_variants(self):
        assert get_lang("uk_UA") == "uk"

    def test_en_code_returns_en(self):
        assert get_lang("en") == "en"

    def test_ru_returns_en(self):
        assert get_lang("ru") == "en"

    def test_none_returns_en(self):
        assert get_lang(None) == "en"

    def test_empty_string_returns_en(self):
        assert get_lang("") == "en"

    def test_arbitrary_lang_returns_en(self):
        assert get_lang("de") == "en"


class TestT:
    @pytest.mark.parametrize("lang", ["uk", "en"])
    def test_welcome_key_returns_string(self, lang):
        result = t("welcome", lang, name="Test")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Test" in result

    def test_uk_welcome_uses_pryvit_not_ni(self):
        result = t("welcome", "uk", name="Олена")
        assert "Привіт" in result
        # Check that it doesn't start with "Ні," which is a mistranslation of "Hi,"
        assert not result.strip().startswith("Ні,")

    @pytest.mark.parametrize("lang", ["uk", "en"])
    def test_ibtn_keys(self, lang):
        for key in ["ibtn_ask_ai", "ibtn_lead", "ibtn_about", "ibtn_language", "ibtn_cancel"]:
            result = t(key, lang)
            assert result == TEXTS[lang][key]
            assert len(result) > 0

    @pytest.mark.parametrize("lang", ["uk", "en"])
    def test_stats_key_contains_placeholders_when_filled(self, lang):
        result = t("stats", lang, total=1, users=2, today=3)
        assert "1" in result
        assert "2" in result
        assert "3" in result

    @pytest.mark.parametrize("lang", ["uk", "en"])
    def test_antispam_warn_key(self, lang):
        result = t("antispam_warn", lang)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_missing_key_returns_key_itself(self):
        result = t("totally_nonexistent_key_xyz", "uk")
        assert result == "totally_nonexistent_key_xyz"

    def test_missing_key_en_returns_key_itself(self):
        result = t("totally_nonexistent_key_xyz", "en")
        assert result == "totally_nonexistent_key_xyz"

    def test_uk_and_en_buttons_differ(self):
        for key in ["ibtn_ask_ai", "ibtn_lead", "ibtn_about", "ibtn_cancel"]:
            assert t(key, "uk") != t(key, "en")

    def test_lang_select_msg_present(self):
        assert isinstance(LANG_SELECT_MSG, str)
        assert len(LANG_SELECT_MSG) > 0
        assert "Оберіть мову" in LANG_SELECT_MSG
        assert "Choose your language" in LANG_SELECT_MSG

    def test_all_keys_mirrored_between_uk_and_en(self):
        uk_keys = set(TEXTS["uk"].keys())
        en_keys = set(TEXTS["en"].keys())
        assert uk_keys == en_keys

    @pytest.mark.parametrize("lang", ["uk", "en"])
    def test_admin_keys_present(self, lang):
        admin_keys = [
            "ibtn_admin",
            "admin_panel_title",
            "admin_btn_stats",
            "admin_btn_docs",
            "admin_btn_upload",
            "admin_btn_clear_all",
            "admin_btn_back",
            "admin_btn_back_main",
            "admin_upload_hint",
            "doc_all_cleared",
            "admin_del_btn",
        ]
        for key in admin_keys:
            assert key in TEXTS[lang]
            val = t(key, lang, filename="sample.pdf", count=3)
            assert len(val) > 0

