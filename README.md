# ComfyUI-DshModel

通过本机 DeepSeek Harness（dsh）服务进行单轮对话的 ComfyUI 自定义节点：多张图片输入做提示词反推，节点内下拉切换所用模型。

Single-turn chat with a local DeepSeek Harness (dsh) service from ComfyUI: multi-image prompt reverse inference with an in-node model dropdown.

## 功能

- **单轮对话**：每次运行新建一个 dsh 会话，发送提示词（可带多张图片），返回模型最终回复文本；
- **多图提示词反推**：`image0…image9` 共 10 个图片输入口（Autogrow，按需添加），逐张转 PNG 发送；不连图片即纯文本对话；
- **模型下拉切换**：`model` 下拉选项由节点注册的路由 `/dshmodel/models` 实时从 dsh `llm.models` 拉取，带刷新按钮，无需重启 ComfyUI；留空使用 dsh 默认模型；
- **零新增依赖**：仅使用 ComfyUI 环境自带的 stdlib / torch / PIL / aiohttp。

## 前置条件

- ComfyUI（本包使用 V3 `io` 节点体系，开发/验证于 ComfyUI 2025+ 版本）；
- 本机 DeepSeek Harness（dsh）Web 服务正在运行，默认 `http://127.0.0.1:3080`，且已配置可用的模型；
- 图片反推需要视觉（VLM）模型，如 `deepseek-official/deepseek-v4-flash-vision-exp` 或 `sglang/Qwen3.8-27B`。

## 安装

1. 将 `ComfyUI-DshModel` 目录整个放入 `ComfyUI/custom_nodes/`；
2. 重启 ComfyUI；
3. 节点菜单搜索 `Dsh`（分类 `dsh`），添加 **Dsh 单轮对话 / 多图反推**。

## 节点参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `prompt` | 文本 | 中文反推指令 | 单轮提示词，可改任意问题；不要以 `/` 开头（dsh 会当斜杠命令执行） |
| `model` | 下拉 | 空（dsh 默认） | `provider/model`；选项实时拉取，点 **refresh** 刷新 |
| `images` | IMAGE ×0–10 | 不连 | Autogrow 输入 `image0…image9`，每个口可接批次；点节点 **+** 添加 |
| `dsh_url` | 文本 | `http://127.0.0.1:3080` | dsh 服务地址 |
| `timeout` | INT | `900`（10–86400） | 等待回合结束的超时秒数 |
| `poll_interval` | FLOAT | `2.0`（0.5–60） | 轮询会话历史的间隔秒数 |

输出：`STRING` —— 模型最终回复（回合内最后一条助手消息的全部文本块）。

## 用法示例

```
Load Image ─┐
Load Image ─┼─(image0/image1)─> Dsh 单轮对话 / 多图反推 ─> Show Text / Save Text
Load Image ─┘
```

`prompt` 用默认值即可反推；想直接对话就把 `prompt` 改成问题、`images` 不连线。

## 工作原理

每次执行：

1. `POST /api/session.create` 新建会话；
2. 若 `model` 非空：`POST /api/session.selectModel`（`provider` / `model` 按第一个 `/` 拆分）；
3. `POST /api/session.prompt` 发送文本 + 所有图片（PNG → base64）；
4. 轮询 `POST /api/session.history` 直到出现 `turn/end` 事件，取该回合最后一条
   `assistant/message` 的文本块拼接为输出；回合以错误结束或超时则报错。

dsh 线格式取自 dsh 源码 `packages/host/apiproxy`：`POST /api/<method>`，
请求体 `{type:'client-request', rpcId, method, payload}`，
响应体 `{type:'server-response', rpcId, result:{ok, value|error}}`。

下拉数据由本包注册的 GET 路由 `/dshmodel/models` 提供（内部代理 dsh
`llm.models`），返回 `["", "provider/model", ...]`（首项空 = dsh 默认模型）。

## 已知限制 / 常见问题

- **每次执行新建一个 dsh 会话**：会在 dsh 会话列表里留下记录，可手动清理；
- **`model` 下拉固定查询 3080 默认端口**：若 `dsh_url` 改成其他端口，下拉列表与实际服务可能不一致（执行仍走 `dsh_url`）；
- **dsh 未启动**：下拉降级为只有"默认模型"一项（路由静默失败），执行时明确报连接错误；
- **图片大小**：dsh 对附件数量和字节数有限制（`imageLimits`），超限时执行报 `attachment-error`，可减小图片分辨率或数量；
- **执行阻塞**：节点会阻塞到回合结束（agent 型回合可能数分钟），用 `timeout` 控制上限。

## 开发 / 测试

```bash
python test_mock_roundtrip.py
```

不启动 ComfyUI / dsh 即可回归全链路：mock dsh 服务上的
建会话 → 选模型 → 发图文提示 → 轮询历史 → 取最终文本、
模型下拉数据源、V3 schema 序列化（`/object_info` 入口）与路由注册。

| 文件 | 说明 |
| --- | --- |
| `__init__.py` | 节点与 dsh HTTP 客户端（全部实现） |
| `test_mock_roundtrip.py` | mock 回归测试 |
| `pyproject.toml` | ComfyUI 注册元数据 |
