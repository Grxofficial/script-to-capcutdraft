from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autocut_agent import platform_adapter, windows_install


class WindowsPlatformTests(unittest.TestCase):
    def test_explicit_draft_root_does_not_depend_on_operating_system(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.assertEqual(platform_adapter.resolve_draft_root(str(root)), root)

    def test_windows_smoke_state_is_isolated_from_macos(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "compat.json"
            state.write_text(json.dumps({
                "passed": True,
                "platform": "windows",
                "app_version": "11.3.0",
            }), encoding="utf-8")
            with patch.object(platform_adapter.sys, "platform", "win32"), \
                 patch("autocut_agent.windows_install.current_jianying_version", return_value="11.3.0"):
                self.assertTrue(platform_adapter.smoke_is_approved(state))
            with patch.object(platform_adapter.sys, "platform", "darwin"), \
                 patch("autocut_agent.mac_install.current_jianying_version", return_value="11.3.0"):
                self.assertFalse(platform_adapter.smoke_is_approved(state))

    def test_windows_prepare_install_and_restore_with_temp_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft_root = root / "drafts"
            staging = root / "staging" / "Windows-冒烟"
            resources_source = root / "source.mp4"
            draft_root.mkdir()
            staging.mkdir(parents=True)
            resources_source.write_bytes(b"video")
            (draft_root / "root_meta_info.json").write_text(
                json.dumps({"all_draft_store": [], "root_path": str(draft_root)}),
                encoding="utf-8",
            )
            content = {
                "duration": 1_000_000,
                "materials": {
                    "videos": [{
                        "path": str(resources_source),
                        "duration": 1_000_000,
                        "material_name": "source.mp4",
                        "width": 1080,
                        "height": 1920,
                    }],
                    "audios": [],
                },
                "platform": {},
                "last_modified_platform": {},
            }
            meta = {
                "draft_id": "WINDOWS-SMOKE",
                "draft_materials": [{"type": 0, "value": []}],
            }
            (staging / "draft_content.json").write_text(json.dumps(content), encoding="utf-8")
            (staging / "draft_meta_info.json").write_text(json.dumps(meta), encoding="utf-8")

            with patch("autocut_agent.windows_install.current_jianying_version", return_value="11.3.0"), \
                 patch("autocut_agent.windows_install.jianying_running", return_value=False), \
                 patch("autocut_agent.windows_install.subprocess.run"):
                info = windows_install.windowsify(
                    staging, "Windows-冒烟", draft_root, allow_missing_fingerprint=True,
                )
                installed_content = json.loads((staging / "draft_info.json").read_text(encoding="utf-8"))
                self.assertEqual(installed_content["platform"]["os"], "windows")
                self.assertIn(str(draft_root / "Windows-冒烟"), installed_content["materials"]["videos"][0]["path"])
                windows_install.install(staging, "Windows-冒烟", draft_root, info)
                installed = draft_root / "Windows-冒烟"
                self.assertTrue((installed / "Resources" / "source.mp4").is_file())
                registry = json.loads((draft_root / "root_meta_info.json").read_text(encoding="utf-8"))
                self.assertEqual(registry["all_draft_store"][0]["draft_name"], "Windows-冒烟")
                windows_install.uninstall("Windows-冒烟", draft_root)
                self.assertFalse(installed.exists())


if __name__ == "__main__":
    unittest.main()
