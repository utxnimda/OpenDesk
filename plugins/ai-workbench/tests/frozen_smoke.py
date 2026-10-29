"""Exercise the packaged stdio identity, authenticated HTTP and drain without any model call."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import queue
import threading
import urllib.request
import urllib.error
from urllib.parse import urlsplit, parse_qs


def main():
    exe, version = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="opendesk-ai-frozen-") as directory:
        child = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 env={**os.environ, "OPENDESK_MODULE_DATA_DIR": directory}, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        replies = queue.Queue()
        def reader():
            for line in child.stdout:
                replies.put(line)
        threading.Thread(target=reader, daemon=True).start()
        def call(method):
            child.stdin.write((json.dumps({"protocolVersion":1,"id":method,"method":method,"params":{}})+"\n").encode());child.stdin.flush()
            response=json.loads(replies.get(timeout=15));assert "error" not in response,response
            return response["result"]
        try:
            capabilities=call("runtime.hello")["capabilities"]
            assert set(capabilities)=={"runtime.hello","runtime.ping","runtime.shutdown","module.identity","module.webapp","module.prepareShutdown","ai.status"}
            assert call("module.identity")=={"id":"ai-workbench","version":version,"hostApi":2}
            status=call("ai.status");url=urlsplit(status["url"]);origin=f"{url.scheme}://{url.netloc}"
            token=parse_qs(url.fragment)["session"][0]
            assert b"<html" in urllib.request.urlopen(origin+"/",timeout=5).read().lower()
            try:urllib.request.urlopen(origin+"/api/state",timeout=5)
            except urllib.error.HTTPError as e:assert e.code==401
            else:raise AssertionError("Unauthenticated request succeeded")
            request=urllib.request.Request(origin+"/api/state",headers={"Authorization":"Bearer "+token,"Origin":origin})
            state=json.load(urllib.request.urlopen(request,timeout=5));assert state["connections"]==[]
            assert call("module.prepareShutdown")=={"ready":True,"active":0}
            call("runtime.shutdown");assert child.wait(timeout=10)==0
            print("AI frozen identity, authenticated HTTP, static UI and drain passed; no model request.")
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)


if __name__=="__main__":main()
