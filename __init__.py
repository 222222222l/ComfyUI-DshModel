import base64
import io as pyio
import json
import time
import urllib.request
import uuid

import torch
from aiohttp import ClientError, ClientSession, ClientTimeout, web
from PIL import Image
from comfy_api.latest import io
from server import PromptServer

DEFAULT_DSH_URL = "http://127.0.0.1:3080"
MODELS_ROUTE = "/dshmodel/models"


def _post(base_url, method, payload):
    body = json.dumps({
        "type": "client-request",
        "rpcId": uuid.uuid4().hex,
        "method": method,
        "payload": payload,
    }).encode()
    request = urllib.request.Request(
        base_url + "/api/" + method, data=body, method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        wire = json.loads(response.read().decode())
    result = wire.get("result", {})
    if not result.get("ok"):
        error = result.get("error", {})
        details = error.get("details")
        suffix = json.dumps(details, ensure_ascii=False) if details else ""
        raise RuntimeError("dsh %s 失败: %s %s" % (method, error.get("message", error.get("code", "")), suffix))
    return result.get("value") or {}


async def _llm_models(base_url):
    payload = {"type": "client-request", "rpcId": uuid.uuid4().hex, "method": "llm.models", "payload": {}}
    try:
        timeout = ClientTimeout(total=8)
        async with ClientSession(timeout=timeout) as client:
            async with client.post(base_url + "/api/llm.models", json=payload) as response:
                wire = await response.json()
    except (OSError, ClientError, ValueError) as error:
        raise RuntimeError("dsh llm.models 请求失败: %s" % error) from error
    result = wire.get("result", {})
    if not result.get("ok"):
        error = result.get("error", {})
        raise RuntimeError("dsh llm.models 失败: %s" % error.get("message", error.get("code", "")))
    return result.get("value") or {}


async def _model_options(base_url=DEFAULT_DSH_URL):
    try:
        value = await _llm_models(base_url)
    except RuntimeError:
        return []
    return [""] + ["%s/%s" % (g["id"], m["id"])
                   for g in value.get("groups", []) for m in g.get("models", [])]


async def _models_route(request):
    return web.json_response(await _model_options())


PromptServer.instance.routes.get(MODELS_ROUTE)(_models_route)


def _create_session(base_url):
    return _post(base_url, "session.create", {})["sessionId"]


def _select_model(base_url, session_id, spec):
    if "/" not in spec:
        raise ValueError("model 需按 provider/model 填写，如 deepseek-official/deepseek-v4-pro；点 model 的 refresh 刷新列表后重选")
    provider, model = spec.split("/", 1)
    _post(base_url, "session.selectModel", {"sessionId": session_id, "provider": provider, "model": model})


def _send_prompt(base_url, session_id, prompt, image_bytes):
    content = [{"type": "text", "text": prompt}]
    for index, data in enumerate(image_bytes):
        content.append({
            "type": "image",
            "mediaType": "image/png",
            "data": base64.b64encode(data).decode(),
            "name": "image_%d.png" % (index + 1),
        })
    _post(base_url, "session.prompt", {"sessionId": session_id, "mode": "queue", "content": content})


def _history(base_url, session_id):
    # session.history 返回 HistoryEntry 包装（{event, view?}），这里解出裸事件
    return [entry["event"] for entry in _post(base_url, "session.history", {"sessionId": session_id}).get("events", [])]


def _last_assistant_text(events):
    text = ""
    for event in events:
        if event.get("type") != "assistant/message":
            continue
        content = event.get("data", {}).get("message", {}).get("content", [])
        blocks = [block["text"] for block in content if block.get("type") == "text" and block.get("text", "").strip()]
        if blocks:
            text = "\n\n".join(blocks)
    return text


def _wait_final_text(base_url, session_id, timeout, poll_interval):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = _history(base_url, session_id)
        turn_end = next((e for e in events if e.get("type") == "turn/end"), None)
        if turn_end is not None:
            reason = turn_end.get("data", {}).get("reason", {})
            if reason.get("kind") == "error":
                raise RuntimeError("dsh 回合以错误结束: %s" % reason.get("error", {}).get("message", "unknown"))
            text = _last_assistant_text(events)
            if not text:
                raise RuntimeError("dsh 回合结束 (%s) 但没有助手文本" % reason.get("kind", "unknown"))
            return text
        time.sleep(poll_interval)
    raise TimeoutError("等待 dsh 回合结束超时（%ds），可用 timeout 输入调大" % timeout)


def _tensor_to_pngs(images):
    buffer = []
    for index in range(images.shape[0]):
        tensor = (images[index].clamp(0, 1) * 255).round().to(torch.uint8).cpu().numpy()
        byte_io = pyio.BytesIO()
        Image.fromarray(tensor).save(byte_io, format="PNG")
        buffer.append(byte_io.getvalue())
    return buffer


class DshPromptInfer(io.ComfyNode):
    """经本地 dsh 服务单轮对话：每次运行新建会话，发文本与多张图片，返回模型最终回复。"""

    @classmethod
    def define_schema(cls):
        images_template = io.Autogrow.TemplatePrefix(io.Image.Input("image"), prefix="image", min=0, max=10)
        return io.Schema(
            node_id="DshPromptInfer",
            display_name="Dsh 单轮对话 / 多图反推",
            category="dsh",
            search_aliases=["dsh", "harness", "提示词反推", "prompt reverse"],
            inputs=[
                io.String.Input("prompt", multiline=True,
                                default="请反推这些图片的提示词：分别给出主体、构图、风格、光影与质量词，直接输出，不要解释。"),
                io.Combo.Input("model", options=[""], default="",
                               remote=io.RemoteOptions(route=MODELS_ROUTE, refresh_button=True, timeout=8000, max_retries=2)),
                io.String.Input("dsh_url", default=DEFAULT_DSH_URL),
                io.Autogrow.Input("images", template=images_template, optional=True),
                io.Int.Input("timeout", default=900, min=10, max=86400, optional=True),
                io.Float.Input("poll_interval", default=2.0, min=0.5, max=60.0, optional=True),
            ],
            outputs=[io.String.Output()],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        # model 选项由 remote 路由实时拉取，schema 里的静态 options 校验不适用；
        # 声明 **kwargs 让 execution 跳过内置 combo 校验，格式在 _select_model 边界报错
        return True

    @classmethod
    def execute(cls, prompt: str, model: str, dsh_url: str, images: dict | None = None,
                timeout: int = 900, poll_interval: float = 2.0) -> io.NodeOutput:
        image_bytes = []
        for tensor in (images or {}).values():
            image_bytes.extend(_tensor_to_pngs(tensor))
        session_id = _create_session(dsh_url)
        if model:
            _select_model(dsh_url, session_id, model)
        _send_prompt(dsh_url, session_id, prompt, image_bytes)
        return io.NodeOutput(_wait_final_text(dsh_url, session_id, timeout, poll_interval))


NODE_CLASS_MAPPINGS = {
    "DshPromptInfer": DshPromptInfer,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DshPromptInfer": "Dsh 单轮对话 / 多图反推",
}
