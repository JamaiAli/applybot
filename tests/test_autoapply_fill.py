import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from applypilot import autoapply
from applypilot import telegram_bot

def test_get_python_exe():
    exe = telegram_bot._get_python_exe()
    assert exe is not None
    assert "python" in exe.lower()
    p = Path(exe)
    assert p.exists()


def test_js_deep_fill_syntax():
    assert "_JS_DEEP_FILL" in dir(autoapply)
    assert "deepQueryAll" in autoapply._JS_DEEP_FILL
    assert "setNativeValue" in autoapply._JS_DEEP_FILL
    assert "InputEvent" in autoapply._JS_DEEP_FILL


def test_js_attach_cv_base64_syntax():
    assert "_JS_ATTACH_CV_BASE64" in dir(autoapply)
    assert "DataTransfer" in autoapply._JS_ATTACH_CV_BASE64


def test_fill_known_fields_calls_evaluate():
    mock_page = MagicMock()
    mock_frame = MagicMock()
    mock_frame.evaluate.return_value = 3
    mock_page.frames = [mock_frame]

    count = autoapply._fill_known_fields(mock_page, {"personal": {"full_name": "Ali Jamai"}})
    assert count == 3
    mock_frame.evaluate.assert_called_once()


def test_run_fill_cycle_flow():
    mock_page = MagicMock()
    mock_page.url = "https://example.com/apply"
    mock_frame = MagicMock()
    mock_frame.evaluate.return_value = 4
    mock_page.frames = [mock_frame]

    with patch.object(autoapply, "_has_captcha", return_value=False), \
         patch.object(autoapply, "_looks_like_login", return_value=False), \
         patch.object(autoapply, "_form_present", return_value=True), \
         patch.object(autoapply, "_fill_selects", return_value=1), \
         patch.object(autoapply, "_smart_fill", return_value=0), \
         patch.object(autoapply, "_upload_cv", return_value=True), \
         patch.object(autoapply, "_set_button_label") as mock_set_label:

        n, up = autoapply._run_fill_cycle(mock_page, {}, {}, "dummy_cv.pdf", set())
        assert n == 5  # 4 + 1
        assert up is True
        mock_set_label.assert_called_once()
        label_text = mock_set_label.call_args[0][1]
        assert "5 champ(s) rempli(s)" in label_text
        assert "CV joint" in label_text
