"""Exercise the packaged GUI worker without loading any user's workspace."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

def main():
    executable, version = sys.argv[1:]
    # WebView2 can release its metrics cache shortly after the GUI process exits.
    # A locked throwaway browser cache must not invalidate the protocol assertions.
    with tempfile.TemporaryDirectory(prefix='nimda-module-smoke-', ignore_cleanup_errors=True) as directory:
        env = dict(os.environ, OPENDESK_MODULE_DATA_DIR=directory)
        for name in ('PYTHONPATH', 'PYTHONHOME', 'MEDIA_MANAGER_WORKSPACE_ROOT', 'MEDIA_MANAGER_APPLICATION_ROOT',
                     'NIMDA_WORKSPACE_ROOT', 'NIMDA_APPLICATION_ROOT'):
            env.pop(name, None)
        with open(Path(directory) / 'stderr.log', 'wb') as errors:
            process = subprocess.Popen([executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                       env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            responses = queue.Queue()
            def read():
                for line in process.stdout:
                    responses.put(line)
                responses.put(None)
            threading.Thread(target=read, daemon=True).start()
            sequence = 0
            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                rid = str(sequence)
                payload = dict(protocolVersion=1, id=rid, method=method, params=params or {})
                process.stdin.write((json.dumps(payload) + '\n').encode())
                process.stdin.flush()
                line = responses.get(timeout=30)
                assert line, 'Worker exited unexpectedly'
                response = json.loads(line)
                assert response.get('id') == rid and response.get('protocolVersion') == 1
                assert 'error' not in response, response.get('error')
                return response['result']
            try:
                assert call('module.identity') == dict(id='nimda-workspace', version=version, hostApi=2)
                hello = call('runtime.hello')
                assert 'module.prepareShutdown' in hello['capabilities']
                url = call('module.webapp')['url']
                assert url.startswith('http://127.0.0.1:') and '#nimda_session=' in url
                origin = url.split('/#')[0]
                with urllib.request.urlopen(origin + '/', timeout=10) as response:
                    assert b'/__opendesk/bootstrap.js' in response.read()
                try:
                    urllib.request.urlopen(origin + '/api/config', timeout=5)
                    raise AssertionError('Unauthenticated API accepted')
                except urllib.error.HTTPError as error:
                    assert error.code == 401
                status = call('nimda.status')
                assert status['configured'] is False and status['url'] == url
                assert call('module.prepareShutdown')['ready'] is True
                assert call('runtime.shutdown')['status'] == 'stopping'
                process.stdin.close()
                assert process.wait(timeout=30) == 0
                print('Frozen worker: identity, HTTP gate, stable URL and graceful GUI shutdown passed.')
            finally:
                if process.poll() is None:
                    # This test never configures a workspace or starts business work.
                    process.kill()
                    process.wait(timeout=10)

if __name__ == '__main__':
    main()
