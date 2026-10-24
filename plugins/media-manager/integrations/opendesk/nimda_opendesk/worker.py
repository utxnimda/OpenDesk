"""Two presentation modes share one backend and one bounded control channel."""
import logging
import os
from pathlib import Path
import sys
import threading
import time

from .controller import Controller, application_root, setup_python
from .native import NativeWindows
from .protocol import ProtocolError, error_response, frames, parse, write


def serve(controller, reader, writer):
    try:
        for frame in frames(reader):
            rid, stop = "", False
            try:
                if isinstance(frame, ProtocolError):
                    raise frame
                request = parse(frame)
                rid = request["id"]
                value = controller.dispatch(request["method"], request["params"])
                stop = request["method"] == "runtime.shutdown"
                response = {"protocolVersion": 1, "id": rid, "result": value}
            except ProtocolError as error:
                if not error.request_id:
                    error.request_id = rid
                response = error_response(error)
            except Exception as error:
                logging.error("作品管理器 module handler failed; category=%s; request payload omitted", type(error).__name__)
                response = error_response(ProtocolError("INTERNAL_ERROR", "作品管理器 could not complete this request.", rid))
            write(writer, response)
            if stop:
                break
    except (BrokenPipeError, OSError):
        logging.warning("作品管理器 host control channel closed")
    finally:
        # EOF and channel loss still drain started writes before releasing the mutex.
        while not controller.prepare_shutdown()["ready"]:
            time.sleep(0.05)
        controller.close()


def main():
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="nimda-module %(levelname)s %(message)s")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    root = application_root(Path(__file__).resolve().parents[1] / "worker_entry.py")
    setup_python(root)
    data = os.environ.get("OPENDESK_MODULE_DATA_DIR", "")
    if not data or not Path(data).is_absolute():
        raise RuntimeError("OpenDesk must provide OPENDESK_MODULE_DATA_DIR")
    native = NativeWindows(Path(data) / "webview")
    controller = Controller(root, data, native)
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    # Third-party GUI libraries must never contaminate protocol stdout.
    sys.stdout = sys.stderr
    service = threading.Thread(target=serve, args=(controller, reader, writer), name="nimda-stdio", daemon=False)
    service.start()
    try:
        native.run(controller)
    except Exception:
        logging.error("作品管理器 native window startup failed")
        controller.error = "作品管理器 原生窗口初始化失败，请检查 WebView2 运行环境。"
        controller.prepare_shutdown()
        # Returning while stdin remains open would hang a normal shutdown. The
        # control thread stays responsive and reports the explicit startup error.
        service.join()
        return 1
    service.join()
    return 0
