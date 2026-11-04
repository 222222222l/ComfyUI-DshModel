# ComfyUI-DshModel

[中文](README.md)

Custom nodes connecting a ComfyUI workflow to a local DeepSeek Harness (DSH) service. Each execution creates a separate session, sends text and optional images, and returns final assistant text after the turn ends. Model capabilities come from the connected service; this repository provides the adapter.

## Requirements and installation

Requires ComfyUI with the V3 `io` node API and a running, configured DSH service. The default address is `http://127.0.0.1:3080`. Image inputs require a vision-capable selected model.

Place the directory under `ComfyUI/custom_nodes/`, restart, and find **Dsh single-turn chat / multi-image prompt inference** in category `dsh`. The package uses stdlib, torch, PIL, and aiohttp from the ComfyUI environment; it provides no model weights.

## Node interface

| Parameter | Meaning |
| --- | --- |
| `prompt` | Text instruction; a leading `/` may be interpreted as a DSH command |
| `model` | `provider/model`; empty uses the DSH default |
| `images` | Dynamic `image0…image9` ports, each accepting batches; no images gives text-only chat |
| `dsh_url` | Execution service address |
| `timeout` | Wait limit: default 900 seconds, range 10–86400 |
| `poll_interval` | History polling: default 2 seconds, range 0.5–60 |

The `STRING` output joins text blocks of the last `assistant/message` in the completed turn. Connect image loaders and a text-view/save node.

Calls proceed through `session.create`, optional `session.selectModel`, `session.prompt`, and `session.history` polling until `turn/end`. Errors and timeouts are reported as execution errors.

## Known limits

- Every execution leaves a new DSH session; automatic cleanup is not implemented.
- The `/dshmodel/models` dropdown queries the default port 3080. With a custom `dsh_url`, the dropdown may not match the execution service.
- An offline service may leave only the default dropdown entry; that is not a successful connection check.
- Attachment limits, model support, and API costs depend on DSH/provider configuration. Prompt inference does not guarantee recovery of an original generation prompt.
- The node waits for the full turn; Agent workloads can block the workflow for an extended period.

## Verification and code

```bash
python test_mock_roundtrip.py
```

Mock tests cover sessions, model selection, image/text submission, history, text extraction, and node/route interfaces. They do not establish end-to-end quality for a real ComfyUI/DSH/vision-model combination.

See [__init__.py](__init__.py) and [test_mock_roundtrip.py](test_mock_roundtrip.py). This is not an official DSH or DeepSeek distribution. No top-level `LICENSE` is currently supplied.
