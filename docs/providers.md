# 火山方舟 Agent Plan

本项目公开版只提供火山方舟 Agent Plan 链路。

## 默认分工

| 能力 | 模型或接口 |
|---|---|
| 关键帧多模态理解（VLM） | `glm-5.3-flash` / Responses API |
| 文案与镜头向量化 | `doubao-embedding-vision` / Embeddings API，每批最多 10 条 |
| 文案语义拆句 | `doubao-seed-2-0-lite` / Chat Completions API |
| 候选镜头重排 | `doubao-seed-2-0-lite` / Chat Completions API |
| 中文旁白 | 豆包语音合成 2.0 Agent Plan 专属接口 |
| 默认音色 | `zh_female_xiaohe_uranus_bigtts` |

默认数据面地址：

```text
https://ark.cn-beijing.volces.com/api/plan/v3
```

语音合成地址、资源 ID 和模型名见 `config.example.toml`。这些值可在 `config.toml` 中覆盖。

## 凭证

项目根目录 `.env` 只需：

```dotenv
AUTOCUT_ARK_API_KEY=
```

程序会将同一把 Key 用于 OpenAI 兼容模型接口和 Agent Plan 专属 TTS 接口。为了兼容私有部署，也保留了 `AUTOCUT_AI_API_KEY` 和 `AUTOCUT_ARK_PLAN_TTS_API_KEY` 的分别覆盖能力，但快速上手不需要配置它们。

## 缓存与费用

- 视觉、向量、拆句和重排响应按完整请求哈希写入 `.autocut/ai-cache/`。
- TTS 原始音频写入 `jobs/<任务>/voice/*.source.wav`。
- 相同请求优先命中缓存；模型、文案、关键帧或候选变化会产生新调用。
- 本项目不提供额度，实际计费和套餐能力以用户自己的火山方舟账户为准。
