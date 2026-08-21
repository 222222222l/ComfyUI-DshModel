"""mock dsh 服务回归：建会话→选模型→发图文提示→轮询历史→取最终文本 + 模型下拉数据源。

python test_mock_roundtrip.py
"""
import asyncio
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_plugin():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    # 生产环境里 PromptServer 实例先于自定义节点导入创建（main.py 顺序）；
    # 独立测试用 stub 提供同样的环境，避免拉起真实 server。
    if "server" not in sys.modules:
        import types

        class _FakeRoutes:
            def __init__(self):
                self.registered = []

            def get(self, path):
                def deco(handler):
                    self.registered.append((path, handler))
                    return handler
                return deco

        stub = types.ModuleType("server")
        stub._fake_routes = _FakeRoutes()
        stub.PromptServer = type("PromptServer", (), {"instance": types.SimpleNamespace(routes=stub._fake_routes)})
        sys.modules["server"] = stub
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
    spec = importlib.util.spec_from_file_location("dshmodel", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MockDsh(BaseHTTPRequestHandler):
    history_calls = 0
    prompts = []
    selected = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        method, payload = self.path.rsplit("/", 1)[1], body["payload"]
        if body["method"] != method:
            self.reply(404, None)
            return
        if method == "llm.models":
            value = {"groups": [{"id": "p1", "name": "P1", "models": [
                {"id": "m-a", "name": "Model A"}, {"id": "m-b", "name": "Model B"}]}], "failures": []}
        elif method == "session.create":
            value = {"sessionId": "sess-1"}
        elif method == "session.selectModel":
            type(self).selected.append(payload)
            value = {"selected": {"provider": payload["provider"], "model": payload["model"]}}
        elif method == "session.prompt":
            type(self).prompts.append(payload)
            value = {"accepted": True}
        elif method == "session.history":
            type(self).history_calls += 1
            if self.history_calls == 1:
                events = [{"event": {"type": "turn/start", "seq": 0, "data": {"turn": 1}}}]
            else:
                events = [
                    {"event": {"type": "user/message", "seq": 0, "data": {}}},
                    {"event": {"type": "turn/start", "seq": 1, "data": {"turn": 1}}},
                    {"event": {"type": "assistant/message", "seq": 2, "data": {"turn": 1, "step": 1, "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "主体：猫"}, {"type": "text", "text": "风格：水彩"}],
                    }}}},
                    {"event": {"type": "turn/end", "seq": 3, "data": {"turn": 1, "reason": {"kind": "completed"}}}},
                ]
            value = {"events": events, "hasMore": False}
        else:
            self.reply(404, None)
            return
        self.reply(200, value)

    def reply(self, status, value):
        wire = {"type": "server-response", "rpcId": "echo",
                "result": {"ok": True, "value": value} if value is not None else {"ok": False, "error": {"code": "internal", "message": "nope", "details": {}}}}
        data = json.dumps(wire).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    import torch

    plugin = load_plugin()
    assert sys.modules["server"]._fake_routes.registered == [(plugin.MODELS_ROUTE, plugin._models_route)]

    # /object_info 对 V3 节点的序列化入口（server.py node_info → GET_NODE_INFO_V1）
    info = plugin.DshPromptInfer.GET_NODE_INFO_V1()
    assert info["name"] == "DshPromptInfer"
    inputs = info["input"]
    assert inputs["required"]["prompt"][0] == "STRING"
    model_type, model_cfg = inputs["required"]["model"]
    assert model_type == "COMBO" and model_cfg["remote"]["route"] == plugin.MODELS_ROUTE
    assert model_cfg["remote"]["refresh_button"] is True
    img_type, img_cfg = inputs["optional"]["images"]
    assert img_type == "COMFY_AUTOGROW_V3"
    template = img_cfg["template"]
    assert (template["prefix"], template["min"], template["max"]) == ("image", 0, 10)
    assert info["output"] == ["STRING"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockDsh)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    options = asyncio.run(plugin._model_options(base))
    assert options == ["", "p1/m-a", "p1/m-b"], options

    img1 = torch.rand(1, 8, 8, 3)
    img2 = torch.rand(2, 8, 8, 3)
    result = plugin.DshPromptInfer.execute(
        prompt="反推", model="p1/m-b", dsh_url=base,
        images={"image0": img1, "image1": img2}, timeout=30, poll_interval=0.1)
    assert result[0] == "主体：猫\n\n风格：水彩", result

    prompt_payload = MockDsh.prompts[0]
    assert prompt_payload["mode"] == "queue" and prompt_payload["sessionId"] == "sess-1"
    assert prompt_payload["content"][0] == {"type": "text", "text": "反推"}
    assert [part["type"] for part in prompt_payload["content"][1:]] == ["image"] * 3
    assert all(part["mediaType"] == "image/png" and part["data"] for part in prompt_payload["content"][1:])
    assert MockDsh.selected == [{"sessionId": "sess-1", "provider": "p1", "model": "m-b"}]

    no_model = plugin.DshPromptInfer.execute(prompt="hi", model="", dsh_url=base, images={}, timeout=30, poll_interval=0.1)
    assert no_model[0] == "主体：猫\n\n风格：水彩", no_model
    assert len(MockDsh.prompts[1]["content"]) == 1

    no_images = plugin.DshPromptInfer.execute(prompt="hi", model="", dsh_url=base, images=None, timeout=30, poll_interval=0.1)
    assert len(MockDsh.prompts[2]["content"]) == 1

    # validate_inputs 只负责让 execution 跳过静态 combo 校验（选项来自 remote 路由）
    assert plugin.DshPromptInfer.validate_inputs(model="") is True
    assert plugin.DshPromptInfer.validate_inputs(model="p1/m-b") is True
    assert plugin.DshPromptInfer.validate_inputs(model="no-slash") is True

    # 格式在运行时边界报错
    try:
        plugin._select_model(base, "sess-1", "no-slash")
        raise AssertionError("应当报 ValueError")
    except ValueError as error:
        assert "provider/model" in str(error)

    # execution.py 跳过内置 combo options 校验的开关：first_real_override 必须能找到自定义校验
    import inspect
    from comfy_api.internal import first_real_override
    vf = first_real_override(plugin.DshPromptInfer, "validate_inputs")
    assert vf is not None
    assert inspect.getfullargspec(vf).varkw is not None

    dead = asyncio.run(plugin._model_options("http://127.0.0.1:1"))
    assert dead == [], dead

    print("OK: model options / prompt+images / selectModel / 无图 / 错误路径 / dsh 离线降级 全部通过")
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
