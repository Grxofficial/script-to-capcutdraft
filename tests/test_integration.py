from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from autocut_agent.compose import create_white_job
from autocut_agent.config import AIConfig, AppConfig, CanvasConfig, DraftConfig, DoubaoTTSConfig, MatchingConfig, MediaConfig
from autocut_agent.pipeline import create_job, install_job
from autocut_agent.smoke import SMOKE_NAME, create_smoke, restore as restore_smoke


def wav_bytes(seconds: int = 2) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(32000)
        handle.writeframes(b"\x00\x00" * 32000 * seconds)
    return output.getvalue()


class IntegrationTests(unittest.TestCase):
    def test_install_existing_job_does_not_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = root / "jobs" / "测试"
            draft_dir = job / "staging" / "AI混剪-测试"
            draft_dir.mkdir(parents=True)
            result_path = job / "result.json"
            result_path.write_text(json.dumps({
                "job_dir": str(job),
                "draft_dir": str(draft_dir),
                "installed": False,
                "install_blocked": "等待冒烟",
            }), encoding="utf-8")
            config = AppConfig(
                root=root,
                state_dir=root / ".autocut",
                jobs_dir=root / "jobs",
                draft=DraftConfig(draft_root=str(root / "drafts")),
            )
            info = {
                "fold_path": str(root / "drafts" / "AI混剪-测试"),
                "draft_id": "test",
                "duration": 1,
                "materials_size": 1,
                "tm": 1,
            }
            backup = root / "drafts" / "root_meta_info.json.bak"
            with patch("autocut_agent.pipeline.require_smoke"), \
                 patch("autocut_agent.pipeline.smoke_is_approved", return_value=True), \
                 patch("autocut_agent.pipeline.jianying_running", return_value=False), \
                 patch("autocut_agent.pipeline.prepare_draft", return_value=info) as mocked_prepare, \
                 patch("autocut_agent.pipeline.install", return_value=backup) as mocked_install:
                result = install_job(config, job)
            self.assertTrue(result["installed"])
            self.assertNotIn("install_blocked", result)
            mocked_prepare.assert_called_once()
            mocked_install.assert_called_once()

    def test_offline_end_to_end_builds_three_track_draft(self) -> None:
        descriptions = iter([
            {"caption": "城市街道", "subjects": [], "actions": ["行走"], "scene": "城市", "objects": ["街道"], "emotion": "平静", "shot_scale": "远景", "orientation": "横屏", "text_pollution": 0},
            {"caption": "人物介绍", "subjects": ["人物"], "actions": ["讲解"], "scene": "室内", "objects": [], "emotion": "平静", "shot_scale": "中景", "orientation": "竖屏", "text_pollution": 0},
        ])

        def fake_describe(*_: object) -> dict[str, object]:
            return next(descriptions)

        def fake_embed(_: object, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] if "人物" in text else [0.0, 1.0] for text in texts]

        def fake_synthesize(_: object, __: str, output: Path) -> None:
            output.write_bytes(wav_bytes())

        with patch("autocut_agent.ai.OpenAICompatibleClient.describe_clip", fake_describe), \
             patch("autocut_agent.ai.OpenAICompatibleClient.embed", fake_embed), \
             patch("autocut_agent.tts.DoubaoTTS.synthesize", fake_synthesize):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                library = root / "library"
                library.mkdir()
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=6:size=1080x1920:rate=30", "-pix_fmt", "yuv420p", str(library / "person.mp4"),
                ], check=True)
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=blue:duration=6:size=1920x1080:rate=30", "-pix_fmt", "yuv420p", str(library / "city.mp4"),
                ], check=True)
                script = root / "script.txt"
                script.write_text("人物介绍。城市街道。", encoding="utf-8")
                config = AppConfig(
                    root=root,
                    state_dir=root / ".autocut",
                    jobs_dir=root / "jobs",
                    canvas=CanvasConfig(),
                    media=MediaConfig(scene_backend="ffmpeg", max_shot_seconds=30),
                    ai=AIConfig(base_url="https://invalid.local", api_key="test", vlm_model="fake-vlm", embedding_model="fake-embedding"),
                    doubao_tts=DoubaoTTSConfig(
                        base_url="https://invalid.local/tts",
                        api_key="test",
                        tighten_audio=False,
                    ),
                    matching=MatchingConfig(confidence_threshold=0.55),
                    draft=DraftConfig(auto_install=False),
                )
                result = create_job(config, script, library, "集成测试", progress=lambda _: None)
                self.assertFalse(result["installed"])
                plan = json.loads((root / "jobs/集成测试/plan.json").read_text(encoding="utf-8"))
                self.assertEqual(len(plan["units"]), 2)
                content = json.loads(
                    (root / "jobs/集成测试/staging/AI混剪-集成测试/draft_content.json").read_text(encoding="utf-8")
                )
                tracks = {track["type"]: track for track in content["tracks"]}
                self.assertEqual(set(tracks), {"video", "audio", "text"})
                self.assertEqual(len(tracks["audio"]["segments"]), 2)
                self.assertEqual(len(tracks["text"]["segments"]), 2)
                self.assertTrue(all(segment["volume"] == 0 for segment in tracks["video"]["segments"]))

    def test_compose_white_background_job_builds_three_track_draft(self) -> None:
        def fake_synthesize(_: object, __: str, output: Path) -> None:
            output.write_bytes(wav_bytes(2))

        with patch("autocut_agent.tts.DoubaoTTS.synthesize", fake_synthesize):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                script = root / "script.txt"
                script.write_text("第一句话。第二句话。", encoding="utf-8")
                config = AppConfig(
                    root=root,
                    state_dir=root / ".autocut",
                    jobs_dir=root / "jobs",
                    doubao_tts=DoubaoTTSConfig(
                        base_url="https://invalid.local/tts",
                        api_key="test",
                        tighten_audio=False,
                    ),
                    draft=DraftConfig(auto_install=False),
                )
                result = create_white_job(
                    config, script, "白底测试",
                    install=False, progress=lambda _: None,
                )
                job = root / "jobs" / "白底测试"
                self.assertTrue((job / "assets" / "white_1080x1920.png").is_file())
                plan = json.loads((job / "plan.json").read_text(encoding="utf-8"))
                self.assertEqual(len(plan["units"]), 2)
                self.assertEqual(plan["library_path"], "")
                content = json.loads(
                    (job / "staging" / "AI混剪-白底测试" / "draft_content.json").read_text(encoding="utf-8")
                )
                tracks = {track["type"]: track for track in content["tracks"]}
                self.assertEqual(set(tracks), {"video", "audio", "text"})
                self.assertEqual(len(tracks["video"]["segments"]), 2)
                self.assertEqual(len(tracks["audio"]["segments"]), 2)
                self.assertEqual(len(tracks["text"]["segments"]), 2)
                for video_segment, audio_segment in zip(tracks["video"]["segments"], tracks["audio"]["segments"]):
                    self.assertEqual(
                        video_segment["target_timerange"]["duration"],
                        audio_segment["target_timerange"]["duration"],
                    )
                self.assertEqual(result["validation"], {"video_tracks": 1, "audio_tracks": 1, "text_tracks": 1})

    def test_smoke_installer_bundles_and_unregisters_in_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft_root = root / "drafts"
            draft_root.mkdir()
            (draft_root / "root_meta_info.json").write_text(
                json.dumps({"all_draft_store": [], "draft_ids": [], "root_path": str(draft_root)}),
                encoding="utf-8",
            )
            config = AppConfig(
                root=root,
                state_dir=root / ".autocut",
                jobs_dir=root / "jobs",
                draft=DraftConfig(draft_root=str(draft_root)),
            )
            with patch("autocut_agent.mac_install.jianying_running", return_value=False):
                result = create_smoke(config)
            installed = Path(result["draft"])
            self.assertTrue((installed / "draft_info.json").is_file())
            self.assertGreaterEqual(len(list((installed / "Resources").iterdir())), 2)
            registry = json.loads((draft_root / "root_meta_info.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["all_draft_store"][0]["draft_name"], SMOKE_NAME)
            with patch("autocut_agent.mac_install.jianying_running", return_value=False):
                restore_smoke(config)
            registry = json.loads((draft_root / "root_meta_info.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["all_draft_store"], [])
            self.assertFalse(installed.exists())


if __name__ == "__main__":
    unittest.main()
