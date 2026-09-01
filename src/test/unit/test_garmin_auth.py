"""garmin_auth 的單元測試——不含真實憑證，不發出任何網路請求。

重點驗證 CLAUDE.md §🔐 第 2 條：執行階段只讀 token，token 失效時必須
拋出 GarminAuthRequired 要求人工重跑認證，**絕不可**自動改用帳密登入。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.main.python.services import garmin_auth


class TestMaskEmail(unittest.TestCase):
    """email 遮蔽——避免完整帳號進入輸出或 log。"""

    def test_masks_local_part_keeps_domain(self):
        self.assertEqual(garmin_auth._mask_email("someone@example.com"), "s***@example.com")

    def test_masks_string_without_at_sign(self):
        self.assertEqual(garmin_auth._mask_email("nobody"), "n***")

    def test_never_leaks_full_local_part(self):
        masked = garmin_auth._mask_email("longusername@example.com")
        self.assertNotIn("longusername", masked)


class TestGetTokenstore(unittest.TestCase):
    """token 路徑不可 hardcode：參數 > 環境變數 > 預設值。"""

    def test_explicit_argument_wins(self):
        with mock.patch.dict("os.environ", {"GARMINTOKENS": "/from/env"}):
            self.assertEqual(garmin_auth.get_tokenstore("/explicit"), "/explicit")

    def test_falls_back_to_env_var(self):
        with mock.patch.dict("os.environ", {"GARMINTOKENS": "/from/env"}):
            self.assertEqual(garmin_auth.get_tokenstore(None), "/from/env")

    def test_falls_back_to_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(garmin_auth.get_tokenstore(None), garmin_auth.DEFAULT_TOKENSTORE)


class TestLoadClientNeverLogsInWithPassword(unittest.TestCase):
    """load_client() 是執行階段唯一入口，必須只讀 token。"""

    def test_raises_auth_required_when_tokenstore_missing(self):
        with TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "does_not_exist")
            with self.assertRaises(garmin_auth.GarminAuthRequired) as ctx:
                garmin_auth.load_client(missing)
            # 錯誤訊息必須告訴使用者怎麼修
            self.assertIn("garmin_auth login", str(ctx.exception))

    def test_constructs_client_without_credentials(self):
        """Garmin() 必須以零參數建構——傳帳密進去就等於允許密碼登入。"""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "garmin_tokens.json").write_text("{}", encoding="utf-8")
            fake_client = mock.MagicMock()
            fake_garmin_cls = mock.MagicMock(return_value=fake_client)
            with mock.patch.object(
                garmin_auth, "_import_garmin", return_value=fake_garmin_cls
            ):
                result = garmin_auth.load_client(tmp)
            self.assertIs(result, fake_client)
            fake_garmin_cls.assert_called_once_with()  # 零參數，無帳密
            fake_client.login.assert_called_once_with(tmp)

    def test_expired_token_raises_instead_of_password_fallback(self):
        """token 失效時必須拋例外要求人工重跑，不可自動用帳密重登。"""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "garmin_tokens.json").write_text("{}", encoding="utf-8")
            fake_client = mock.MagicMock()
            fake_client.login.side_effect = RuntimeError("token expired")
            fake_garmin_cls = mock.MagicMock(return_value=fake_client)
            with mock.patch.object(
                garmin_auth, "_import_garmin", return_value=fake_garmin_cls
            ):
                with self.assertRaises(garmin_auth.GarminAuthRequired) as ctx:
                    garmin_auth.load_client(tmp)
            msg = str(ctx.exception)
            self.assertIn("garmin_auth login", msg)
            self.assertIn("rate limit", msg)
            # Garmin 只被建構一次（讀 token），沒有第二次帶帳密的嘗試
            fake_garmin_cls.assert_called_once_with()

    def test_underlying_exception_detail_is_not_leaked(self):
        """原始例外訊息可能含 request 細節，不應原樣外流。"""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "garmin_tokens.json").write_text("{}", encoding="utf-8")
            secret = "Bearer super-secret-token-value"
            fake_client = mock.MagicMock()
            fake_client.login.side_effect = RuntimeError(secret)
            fake_garmin_cls = mock.MagicMock(return_value=fake_client)
            with mock.patch.object(
                garmin_auth, "_import_garmin", return_value=fake_garmin_cls
            ):
                with self.assertRaises(garmin_auth.GarminAuthRequired) as ctx:
                    garmin_auth.load_client(tmp)
            self.assertNotIn(secret, str(ctx.exception))
            # 只保留例外類型名稱，足以除錯又不外流內容
            self.assertIn("RuntimeError", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
