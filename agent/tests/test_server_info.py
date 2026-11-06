import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from coder_agent.config import ServerConfig
from coder_agent.llm import ContextInfo, fetch_context_info


def serve(routes: dict):
    """routes: {(method, path): (status, json_body)}"""

    class H(BaseHTTPRequestHandler):
        def _reply(self):
            status, body = routes.get((self.command, self.path), (404, {"error": "not found"}))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self._reply()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def server_cfg():
    def make(srv, model="m:latest"):
        return ServerConfig(base_url=f"http://127.0.0.1:{srv.server_port}/v1", model=model)
    return make


def test_ollama_loaded_model_reports_real_and_maximum_context(server_cfg):
    srv = serve({
        ("GET", "/api/ps"): (200, {"models": [{"name": "m:latest", "context_length": 131072}]}),
        ("POST", "/api/show"): (200, {"parameters": "num_ctx    65536", "model_info": {"qwen35.context_length": 262144}}),
    })
    info = fetch_context_info(server_cfg(srv))
    srv.shutdown()
    assert (info.source, info.loaded, info.model_max, info.loaded_is_configured) == ("ollama", 131072, 262144, False)
    assert "131,072" in info.describe() and "262,144" in info.describe()


def test_ollama_model_name_without_tag_matches(server_cfg):
    srv = serve({
        ("GET", "/api/ps"): (200, {"models": [{"name": "m:latest", "context_length": 8192}]}),
        ("POST", "/api/show"): (200, {"model_info": {}}),
    })
    assert fetch_context_info(server_cfg(srv, model="m")).loaded == 8192
    srv.shutdown()


def test_ollama_not_loaded_falls_back_to_modelfile_num_ctx(server_cfg):
    srv = serve({
        ("GET", "/api/ps"): (200, {"models": []}),
        ("POST", "/api/show"): (200, {"parameters": "temperature 0.6\nnum_ctx                        98304",
                                      "model_info": {"llama.context_length": 131072}}),
    })
    info = fetch_context_info(server_cfg(srv))
    srv.shutdown()
    assert info.loaded == 98304 and info.loaded_is_configured and info.model_max == 131072
    assert "configured" in info.describe()


def test_vllm_max_model_len(server_cfg):
    srv = serve({("GET", "/v1/models"): (200, {"data": [{"id": "m:latest", "max_model_len": 32768}]})})
    info = fetch_context_info(server_cfg(srv))
    srv.shutdown()
    assert (info.source, info.loaded, info.model_max) == ("vllm", 32768, 32768)


def test_server_without_context_info_and_unreachable_server(server_cfg):
    srv = serve({("GET", "/v1/models"): (200, {"data": [{"id": "m:latest"}]})})
    info = fetch_context_info(server_cfg(srv))
    srv.shutdown()
    assert info.loaded is None and info.error and "unavailable" in info.describe()

    dead = fetch_context_info(ServerConfig(base_url="http://127.0.0.1:9/v1", model="m"), timeout=1)
    assert dead.loaded is None and dead.error
    assert isinstance(dead, ContextInfo)
