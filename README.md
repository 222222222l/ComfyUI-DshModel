# ComfyUI-DshModel

[English](README.en.md)

将 ComfyUI 工作流接入本机 DeepSeek Harness（DSH）的自定义节点。每次运行创建独立会话，发送文本和可选图片，等待回合结束后返回最终助手文本；模型能力来自所连接服务，本仓库实现的是适配层。

## 环境与安装

需要支持 V3 `io` 节点接口的 ComfyUI、正在运行且已配置模型的 DSH 服务。默认地址为 `http://127.0.0.1:3080`；图片输入要求实际选中的模型支持视觉。

将目录放入 `ComfyUI/custom_nodes/` 后重启，在 `dsh` 分类搜索 **Dsh 单轮对话 / 多图反推**。本包复用 ComfyUI 环境中的 stdlib、torch、PIL 与 aiohttp，不提供模型权重。

## 节点接口

| 参数 | 含义 |
| --- | --- |
| `prompt` | 文本指令；以 `/` 开头可能被 DSH 解释为命令 |
| `model` | `provider/model`；空值使用 DSH 默认模型 |
| `images` | `image0…image9` 动态输入口，各口可接批次；无图片时为文本对话 |
| `dsh_url` | 实际执行服务地址 |
| `timeout` | 等待上限，默认900秒，范围10–86400 |
| `poll_interval` | 历史轮询间隔，默认2秒，范围0.5–60 |

输出为 `STRING`，取结束回合中最后一条 `assistant/message` 的文本块。连接图像加载节点和文本查看/保存节点即可使用。

每次调用依次执行 `session.create`、可选 `session.selectModel`、`session.prompt`，再轮询 `session.history` 直到 `turn/end`。错误或超时会作为执行错误返回。

## 已知限制

- 每次执行都会留下新 DSH 会话，当前不会自动清理。
- 模型下拉经 `/dshmodel/models` 查询默认3080端口；自定义 `dsh_url` 时，下拉列表与执行服务可能不一致。
- 服务离线时下拉可能只显示默认项，不能据此判定连接成功。
- 图片字节数、附件数、模型支持和 API 费用取决于 DSH/服务端配置；“反推提示词”不保证还原原始生成指令。
- 节点等待整个回合完成，Agent型任务可能长时间占用工作流。

## 验证与代码

```bash
python test_mock_roundtrip.py
```

Mock 测试覆盖会话、模型选择、图文提交、历史读取、输出拼接及节点/路由接口；不等于真实 ComfyUI、DSH 和视觉模型组合已通过端到端质量验收。

实现见 [__init__.py](__init__.py)，测试见 [test_mock_roundtrip.py](test_mock_roundtrip.py)。本项目不代表 DSH 或 DeepSeek 官方发行；本仓库当前未提供顶层 `LICENSE`。
