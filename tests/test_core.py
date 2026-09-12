from __future__ import annotations

import base64
import io
import json
import math
import os
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from autocut_agent.ai import OpenAICompatibleClient, _parse_response_json
from autocut_agent.batch import _existing_scripts, normalized_script, split_markdown_scripts
from autocut_agent.config import AIConfig, AppConfig, CanvasConfig, DraftConfig, DoubaoTTSConfig, MatchingConfig, MediaConfig, load_config
from autocut_agent.db import LibraryDB
from autocut_agent.errors import ExternalServiceError
from autocut_agent.matching import TimelinePlanner, cosine_similarity, fill_scale
from autocut_agent.media import enforce_boundaries
from autocut_agent.models import ClipRecord, ScriptUnit
from autocut_agent.report import to_srt
from autocut_agent.script import clean_segment_text, normalize_for_compare, split_script, split_units
from autocut_agent.tts import DoubaoTTS, probe_media_audio, tighten_audio
from autocut_agent.webapp import _find_preview_video


class FakeAI(OpenAICompatibleClient):
    def __init__(self):
        super().__init__(AIConfig(api_key="test", embedding_model="test"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "人物" in text else [0.0, 1.0] for text in texts]


class FailingRerankAI(FakeAI):
    def __init__(self):
        super().__init__()
        self.config.rerank_model = "slow-model"

    def rerank(self, text: str, candidates: list[object]) -> list[int]:
        raise ExternalServiceError("读取超时")


class CoreTests(unittest.TestCase):
    def test_shared_ark_key_configures_ai_and_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"AUTOCUT_ARK_API_KEY": "shared-test-key"}, clear=True,
        ):
            config = load_config(Path(temp))
        self.assertEqual(config.ai.api_key, "shared-test-key")
        self.assertEqual(config.doubao_tts.api_key, "shared-test-key")

    def test_markdown_batch_split_and_normalize(self) -> None:
        source = "  第一句  \n第二句\n\n---\n\n第三句\n---\n"
        self.assertEqual(split_markdown_scripts(source), ["第一句  \n第二句", "第三句"])
        self.assertEqual(normalized_script(split_markdown_scripts(source)[0]), "第一句\n第二句")

    def test_batch_only_skips_completed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs = Path(temp)
            failed = jobs / "failed"
            completed = jobs / "completed"
            failed.mkdir()
            completed.mkdir()
            (failed / "script.original.txt").write_text("未完成", encoding="utf-8")
            (completed / "script.original.txt").write_text("已完成", encoding="utf-8")
            (completed / "result.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_existing_scripts(jobs), {"已完成"})

    def test_responses_api_extracts_json(self) -> None:
        response = {
            "output": [
                {"type": "reasoning", "content": []},
                {"type": "message", "content": [{"type": "output_text", "text": "```json\n{\"ok\":true}\n```"}]},
            ]
        }
        self.assertEqual(_parse_response_json(response), {"ok": True})

    def test_responses_api_uses_native_image_content(self) -> None:
        client = OpenAICompatibleClient(AIConfig(
            base_url="https://example.invalid/v3",
            api_key="test",
            api_mode="responses",
            vlm_model="vision-model",
        ))
        fake_response = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"caption\":\"测试\"}"}]}]
        }
        with tempfile.TemporaryDirectory() as temp:
            frame = Path(temp) / "frame.jpg"
            frame.write_bytes(b"jpeg")
            with patch("autocut_agent.ai.post_json", return_value=fake_response) as mocked:
                result = client.describe_clip([frame])
        self.assertEqual(result["caption"], "测试")
        url, payload, _, _ = mocked.call_args.args
        self.assertEqual(url, "https://example.invalid/v3/responses")
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        self.assertTrue(payload["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,"))

    def test_text_model_can_override_responses_with_chat_completions(self) -> None:
        client = OpenAICompatibleClient(AIConfig(
            base_url="https://example.invalid/v3",
            api_key="test",
            api_mode="responses",
            rerank_api_mode="chat_completions",
        ))
        fake_response = {"choices": [{"message": {"content": "{\"ok\":true}"}}]}
        with patch("autocut_agent.ai.post_json", return_value=fake_response) as mocked:
            result = client._model_json("text-model", "测试", api_mode=client.config.rerank_api_mode)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mocked.call_args.args[0], "https://example.invalid/v3/chat/completions")

    def test_identical_ai_request_uses_local_cache(self) -> None:
        fake_response = {"choices": [{"message": {"content": "{\"ok\":true}"}}]}
        with tempfile.TemporaryDirectory() as temp:
            client = OpenAICompatibleClient(
                AIConfig(base_url="https://example.invalid/v3", api_key="test"),
                Path(temp),
            )
            with patch("autocut_agent.ai.post_json", return_value=fake_response) as mocked:
                first = client._model_json("text-model", "测试")
                second = client._model_json("text-model", "测试")
        self.assertEqual(first, second)
        self.assertEqual(mocked.call_count, 1)

    def test_embedding_requests_are_batched(self) -> None:
        client = OpenAICompatibleClient(AIConfig(
            base_url="https://example.invalid/v3",
            api_key="test",
            embedding_model="embedding-model",
            embedding_batch_size=10,
        ))

        def fake_post(_: str, payload: dict[str, object], __: dict[str, str], ___: int) -> dict[str, object]:
            inputs = payload["input"]
            assert isinstance(inputs, list)
            return {
                "data": [
                    {"index": index, "embedding": [float(index), 1.0]}
                    for index, _ in enumerate(inputs)
                ]
            }

        with patch("autocut_agent.ai.post_json", side_effect=fake_post) as mocked:
            vectors = client.embed([f"句子{index}" for index in range(16)])
        self.assertEqual(len(vectors), 16)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(mocked.call_args_list[0].args[1]["input"]), 10)
        self.assertEqual(len(mocked.call_args_list[1].args[1]["input"]), 6)

    def test_split_script_preserves_text_order(self) -> None:
        source = "第一句话，很短。第二句话比较长，需要继续表达！\n第三句？"
        units = split_script(source, max_chars=12)
        joined = "".join(unit.text for unit in units)
        self.assertEqual(normalize_for_compare(joined), normalize_for_compare(source))
        self.assertTrue(all(unit.text for unit in units))
        self.assertNotIn("，", joined)

    def test_split_script_breaks_at_commas(self) -> None:
        source = (
            "周末天气很好，我们带着相机去了海边。"
            "沿着栈道慢慢往前走，远处的海浪一层层靠近岸边，"
            "夕阳落下之前刚好拍完最后一个镜头。"
        )
        units = split_script(source)
        self.assertEqual(
            [unit.text for unit in units],
            [
                "周末天气很好",
                "我们带着相机去了海边",
                "沿着栈道慢慢往前走",
                "远处的海浪一层层靠近岸边",
                "夕阳落下之前刚好拍完最后一个镜头",
            ],
        )

    def test_clean_segment_text_strips_punct_and_emoji(self) -> None:
        self.assertEqual(clean_segment_text("一人小酌刚刚好，"), "一人小酌刚刚好")
        self.assertEqual(clean_segment_text("海边散步首选🌊，"), "海边散步首选")
        self.assertEqual(clean_segment_text("3.6 度微醺度数。"), "3.6 度微醺度数")
        self.assertEqual(clean_segment_text("宅家追剧、夜宵撸串，"), "宅家追剧 夜宵撸串")

    def test_split_units_uses_validated_llm_segments(self) -> None:
        source = (
            "周末去海边散步🌊夕阳照在栈道上，远处有一艘小船，"
            "沿着海岸慢慢走，刚好拍下最后一束光。"
        )
        expected = [
            "周末去海边散步", "夕阳照在栈道上", "远处有一艘小船",
            "沿着海岸慢慢走", "刚好拍下最后一束光",
        ]

        class FakeSegmenter:
            def segment_script(self, _: str) -> list[str]:
                return list(expected)

        units = split_units(source, FakeSegmenter())
        self.assertEqual([unit.text for unit in units], expected)

    def test_split_units_falls_back_when_llm_rewrites(self) -> None:
        source = "今天的海风很舒服。夕阳落下时更漂亮。"

        class CheatingSegmenter:
            def segment_script(self, _: str) -> list[str]:
                return ["今天的天气特别好", "夕阳落下时更漂亮"]  # 改写了原文，必须拒绝

        units = split_units(source, CheatingSegmenter())
        self.assertEqual(
            [unit.text for unit in units],
            ["今天的海风很舒服", "夕阳落下时更漂亮"],
        )

    def test_split_units_falls_back_when_llm_fails(self) -> None:
        class BrokenSegmenter:
            def segment_script(self, _: str) -> None:
                raise RuntimeError("接口超时")

        units = split_units("第一句话。第二句话。", BrokenSegmenter())
        self.assertEqual([unit.text for unit in units], ["第一句话", "第二句话"])

    def test_boundary_constraints(self) -> None:
        clips = enforce_boundaries([0.2, 2.0, 9.0], duration=11.0, minimum=1.0, maximum=4.0)
        self.assertEqual(clips[0][0], 0.0)
        self.assertEqual(clips[-1][1], 11.0)
        self.assertTrue(all(end - start <= 4.001 for start, end in clips))

    def test_similarity_and_fill_scale(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(fill_scale(1920, 1080, 1080, 1920), 3.1604938, places=5)
        self.assertEqual(fill_scale(1080, 1920, 1080, 1920), 1.0)

    def test_timeline_has_no_gaps_or_loops(self) -> None:
        clips = [
            ClipRecord(1, 1, "/tmp/人物.mp4", 0, 6, 1080, 1920, "人物讲解", {}, [1, 0]),
            ClipRecord(2, 2, "/tmp/城市.mp4", 0, 6, 1920, 1080, "城市街道", {}, [0, 1]),
        ]
        units = [
            ScriptUnit(1, "人物介绍。", "/tmp/1.wav", 2.0),
            ScriptUnit(2, "城市街道。", "/tmp/2.wav", 2.0),
        ]
        plan = TimelinePlanner(MatchingConfig(), FakeAI()).create_plan(
            "测试", "/tmp/script.txt", "/tmp/library", units, clips,
            {"width": 1080, "height": 1920, "fps": 30},
        )
        self.assertEqual(plan.units[0].clips[0].clip_id, 1)
        self.assertEqual(plan.units[1].clips[0].clip_id, 2)
        self.assertEqual(plan.units[0].clips[0].timeline_start, 0.0)
        self.assertAlmostEqual(plan.units[0].clips[0].source_start, 10 / 30, places=3)
        self.assertEqual(plan.units[1].clips[0].timeline_start, 2.0)
        self.assertIn("00:00:02,000 --> 00:00:04,000", to_srt(plan))

    def test_rerank_failure_falls_back_to_vector_order(self) -> None:
        clips = [
            ClipRecord(1, 1, "/tmp/人物.mp4", 0, 6, 1080, 1920, "人物讲解", {}, [1, 0]),
            ClipRecord(2, 2, "/tmp/城市.mp4", 0, 6, 1080, 1920, "城市街道", {}, [0, 1]),
        ]
        plan = TimelinePlanner(MatchingConfig(), FailingRerankAI()).create_plan(
            "降级测试", "/tmp/script.txt", "/tmp/library",
            [ScriptUnit(1, "人物介绍。", "/tmp/1.wav", 2.0)], clips,
            {"width": 1080, "height": 1920, "fps": 30},
        )
        self.assertEqual(plan.units[0].clips[0].clip_id, 1)
        self.assertTrue(any("本地向量排序" in warning for warning in plan.units[0].warnings))
        self.assertNotIn("LLM 重排", plan.units[0].clips[0].reason)

    def test_doubao_tts_synthesize_parses_chunked_base64(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mp3 = root / "sample.mp3"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                "-b:a", "64k", str(mp3),
            ], check=True)
            encoded = base64.b64encode(mp3.read_bytes()).decode("ascii")
            split_at = len(encoded) // 2
            split_at -= split_at % 4
            lines = [
                json.dumps({"code": 0, "data": encoded[:split_at]}),
                json.dumps({"code": 0, "data": encoded[split_at:]}),
                json.dumps({"code": 20000000, "data": ""}),
            ]
            response = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
            tts = DoubaoTTS(DoubaoTTSConfig(api_key="test"))
            output = root / "0001.source.wav"
            with patch("autocut_agent.tts.post_json_lines", return_value=response):
                tts.synthesize("测试句子。", output)
            self.assertGreater(probe_media_audio(output), 0.4)

    def test_doubao_tts_synthesize_raises_on_error_line(self) -> None:
        lines = [json.dumps({"code": 3001, "message": "鉴权失败"})]
        response = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
        tts = DoubaoTTS(DoubaoTTSConfig(api_key="test"))
        with patch("autocut_agent.tts.post_json_lines", return_value=response), \
             tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ExternalServiceError):
                tts.synthesize("测试句子。", Path(temp) / "out.wav")

    def test_tighten_audio_removes_only_outer_silence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.wav"
            output = root / "tight.wav"
            sample_rate = 32000
            samples: list[int] = []
            for seconds, voiced in ((0.3, False), (0.35, True), (0.12, False), (0.35, True), (0.3, False)):
                for index in range(int(sample_rate * seconds)):
                    value = int(12000 * math.sin(2 * math.pi * 440 * index / sample_rate)) if voiced else 0
                    samples.append(value)
            with wave.open(str(source), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(b"".join(struct.pack("<h", value) for value in samples))
            tighten_audio(
                source, output,
                threshold_db=-55,
                keep_seconds=0.04,
                sample_rate=32000,
            )
            duration = probe_media_audio(output)
        # 两段语音之间的 120ms 停顿必须保留，避免误删低音量字头。
        self.assertGreater(duration, 0.85)
        self.assertLess(duration, 1.0)

    def test_fractional_audio_durations_do_not_overlap_after_rounding(self) -> None:
        clips = [
            ClipRecord(1, 1, "/tmp/人物.mp4", 0, 20, 1080, 1920, "人物", {}, [1, 0]),
            ClipRecord(2, 2, "/tmp/城市.mp4", 0, 20, 1080, 1920, "城市", {}, [0, 1]),
        ]
        units = [
            ScriptUnit(1, "人物。", "/tmp/1.wav", 2.2985),
            ScriptUnit(2, "城市。", "/tmp/2.wav", 2.2525),
            ScriptUnit(3, "人物。", "/tmp/3.wav", 1.9965),
        ]
        plan = TimelinePlanner(MatchingConfig(), FakeAI()).create_plan(
            "舍入测试", "/tmp/script.txt", "/tmp/library", units, clips,
            {"width": 1080, "height": 1920, "fps": 30},
        )
        for previous, current in zip(plan.units, plan.units[1:]):
            self.assertEqual(
                round(previous.timeline_start + previous.duration, 3),
                current.timeline_start,
            )
        self.assertEqual([unit.duration for unit in plan.units], [2.298, 2.252, 1.996])

    def test_database_prunes_only_missing_library_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database_path = root / "index.sqlite3"
            video = root / "a.mp4"
            video.write_bytes(b"test")
            from autocut_agent.models import MediaInfo
            with LibraryDB(database_path) as database:
                database.replace_file(
                    root, MediaInfo(video, 2, 1080, 1920, 30), "hash", 4, 1, "v1",
                    [{"source_start": 0, "source_end": 2, "caption": "人物", "metadata": {}, "embedding": [1, 0]}],
                )
                self.assertEqual(database.stats(root), {"files": 1, "clips": 1})
                self.assertEqual(database.prune_missing(root, set()), 1)
                self.assertEqual(database.stats(root), {"files": 0, "clips": 0})

    def test_database_combines_multiple_material_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "first", root / "second"
            first.mkdir(); second.mkdir()
            videos = [first / "a.mp4", second / "b.mov"]
            for video in videos:
                video.write_bytes(b"test")
            from autocut_agent.models import MediaInfo
            with LibraryDB(root / "index.sqlite3") as database:
                for index, (library, video) in enumerate(zip((first, second), videos), start=1):
                    database.replace_file(
                        library, MediaInfo(video, 2, 1080, 1920, 30), f"hash-{index}", 4, 1, "v1",
                        [{"source_start": 0, "source_end": 2, "caption": video.stem,
                          "metadata": {}, "embedding": [1, 0]}],
                    )
                clips = database.clips_for_libraries([first, second, first])
            self.assertEqual([Path(clip.path).name for clip in clips], ["a.mp4", "b.mov"])

    def test_preview_video_is_supported_and_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hidden = root / ".cache"
            hidden.mkdir()
            (hidden / "a.mp4").write_bytes(b"hidden")
            (root / "notes.txt").write_text("不是视频", encoding="utf-8")
            expected = root / "sample.mov"
            expected.write_bytes(b"video")
            self.assertEqual(_find_preview_video(root), expected.resolve())


if __name__ == "__main__":
    unittest.main()
