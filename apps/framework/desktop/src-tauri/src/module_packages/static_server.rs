//! Read-only, per-activation static files on an isolated loopback origin.
//! This server exposes no worker, filesystem, or other host command endpoint.
use std::{
    fs::{self, File},
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Component, Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const MAX_REQUEST: usize = 8 * 1024;
const MAX_ASSET: u64 = 32 * 1024 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(2);

pub struct StaticServer {
    url: String,
    address: SocketAddr,
    stopping: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl StaticServer {
    pub fn start(frontend_root: PathBuf, entry_relative: &str) -> Result<Self, String> {
        let root = frontend_root
            .canonicalize()
            .map_err(|_| "MODULE_UI_ROOT_INVALID")?;
        if !root.is_dir() {
            return Err("MODULE_UI_ROOT_INVALID".into());
        }
        let entry = relative_path(entry_relative).ok_or("MODULE_UI_ENTRY_INVALID")?;
        checked_file(&root, &entry).map_err(|_| "MODULE_UI_ENTRY_INVALID")?;
        let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|_| "MODULE_UI_BIND_FAILED")?;
        let address = listener.local_addr().map_err(|_| "MODULE_UI_BIND_FAILED")?;
        listener
            .set_nonblocking(true)
            .map_err(|_| "MODULE_UI_BIND_FAILED")?;
        let prefix = format!("/{}/", uuid::Uuid::new_v4().simple());
        let origin = format!("http://{address}");
        let url = format!("{origin}{prefix}{}", encode_path(entry_relative));
        // Explicit URL sources also work for an iframe without allow-same-origin.
        let source = format!("{origin}{prefix}");
        let csp = format!(
            "sandbox allow-scripts allow-same-origin allow-downloads; default-src 'none'; script-src {source}; \
             style-src {source} 'unsafe-inline'; img-src {source} data: blob:; \
             font-src {source} data:; connect-src 'none'; worker-src 'none'; \
             frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
        );
        let stopping = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&stopping);
        let host = address.to_string();
        let handle = thread::Builder::new()
            .name("module-ui-static".into())
            .spawn(move || {
                while !signal.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            if signal.load(Ordering::Acquire) {
                                break;
                            }
                            let _ = stream.set_read_timeout(Some(Duration::from_millis(200)));
                            let _ = stream.set_write_timeout(Some(Duration::from_millis(200)));
                            // A failed/disconnected client never changes module state.
                            let _ = serve(&mut stream, &root, &prefix, &host, &csp, &signal);
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(15));
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                        Err(_) => break,
                    }
                }
            })
            .map_err(|_| "MODULE_UI_THREAD_FAILED")?;
        Ok(Self {
            url,
            address,
            stopping,
            thread: Some(handle),
        })
    }

    pub fn url(&self) -> &str {
        &self.url
    }
}

impl Drop for StaticServer {
    fn drop(&mut self) {
        self.stopping.store(true, Ordering::Release);
        let _ = TcpStream::connect_timeout(&self.address, Duration::from_millis(100));
        if let Some(handle) = self.thread.take() {
            let _ = handle.join();
        }
    }
}

fn serve(
    stream: &mut TcpStream,
    root: &Path,
    prefix: &str,
    host: &str,
    csp: &str,
    stopping: &AtomicBool,
) -> std::io::Result<()> {
    let deadline = Instant::now() + REQUEST_TIMEOUT;
    let mut bytes = Vec::with_capacity(1024);
    let mut buffer = [0u8; 1024];
    while !bytes.windows(4).any(|part| part == b"\r\n\r\n") {
        if stopping.load(Ordering::Acquire) || Instant::now() >= deadline {
            return Ok(());
        }
        match stream.read(&mut buffer) {
            Ok(0) => return Ok(()),
            Ok(count) => {
                bytes.extend_from_slice(&buffer[..count]);
                if bytes.len() > MAX_REQUEST {
                    return respond(
                        stream,
                        "431 Request Header Fields Too Large",
                        "text/plain",
                        b"Request too large",
                        false,
                        csp,
                        stopping,
                    );
                }
            }
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock
                        | std::io::ErrorKind::TimedOut
                        | std::io::ErrorKind::Interrupted
                ) =>
            {
                continue
            }
            Err(error) => return Err(error),
        }
    }
    let request = match std::str::from_utf8(&bytes) {
        Ok(value) => value,
        Err(_) => {
            return respond(
                stream,
                "400 Bad Request",
                "text/plain",
                b"Invalid request",
                false,
                csp,
                stopping,
            )
        }
    };
    let mut lines = request.split("\r\n");
    let fields: Vec<_> = lines.next().unwrap_or_default().split(' ').collect();
    let head = fields.first() == Some(&"HEAD");
    if fields.len() != 3 || !matches!(fields[2], "HTTP/1.1" | "HTTP/1.0") {
        return respond(
            stream,
            "400 Bad Request",
            "text/plain",
            b"Invalid request",
            head,
            csp,
            stopping,
        );
    }
    if !matches!(fields[0], "GET" | "HEAD") {
        return respond(
            stream,
            "405 Method Not Allowed",
            "text/plain",
            b"GET or HEAD required",
            head,
            csp,
            stopping,
        );
    }
    let mut host_seen = false;
    for line in lines.take_while(|line| !line.is_empty()) {
        let Some((name, value)) = line.split_once(':') else {
            return respond(
                stream,
                "400 Bad Request",
                "text/plain",
                b"Invalid header",
                head,
                csp,
                stopping,
            );
        };
        if name.eq_ignore_ascii_case("host") {
            if host_seen || value.trim() != host {
                return respond(
                    stream,
                    "403 Forbidden",
                    "text/plain",
                    b"Invalid host",
                    head,
                    csp,
                    stopping,
                );
            }
            host_seen = true;
        }
        if name.eq_ignore_ascii_case("transfer-encoding")
            || (name.eq_ignore_ascii_case("content-length") && value.trim() != "0")
        {
            return respond(
                stream,
                "400 Bad Request",
                "text/plain",
                b"Body not supported",
                head,
                csp,
                stopping,
            );
        }
    }
    if !host_seen {
        return respond(
            stream,
            "403 Forbidden",
            "text/plain",
            b"Host required",
            head,
            csp,
            stopping,
        );
    }
    let raw_path = fields[1].split('?').next().unwrap_or_default();
    let relative = raw_path
        .strip_prefix(prefix)
        .and_then(decode_path)
        .and_then(|path| relative_path(&path));
    let Some(relative) = relative else {
        return respond(
            stream,
            "404 Not Found",
            "text/plain",
            b"Not found",
            head,
            csp,
            stopping,
        );
    };
    let path = match checked_file(root, &relative) {
        Ok(path) => path,
        Err(_) => {
            return respond(
                stream,
                "404 Not Found",
                "text/plain",
                b"Not found",
                head,
                csp,
                stopping,
            )
        }
    };
    let mut file = match File::open(&path) {
        Ok(file) => file,
        Err(_) => {
            return respond(
                stream,
                "404 Not Found",
                "text/plain",
                b"Not found",
                head,
                csp,
                stopping,
            )
        }
    };
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() > MAX_ASSET {
        return respond(
            stream,
            "413 Content Too Large",
            "text/plain",
            b"Asset too large",
            head,
            csp,
            stopping,
        );
    }
    let mut content = Vec::with_capacity(metadata.len() as usize);
    // Bound the read even if a local process changes the file after metadata().
    Read::by_ref(&mut file)
        .take(MAX_ASSET + 1)
        .read_to_end(&mut content)?;
    if content.len() as u64 > MAX_ASSET {
        return respond(
            stream,
            "413 Content Too Large",
            "text/plain",
            b"Asset too large",
            head,
            csp,
            stopping,
        );
    }
    respond(stream, "200 OK", mime(&path), &content, head, csp, stopping)
}

fn respond(
    stream: &mut TcpStream,
    status: &str,
    mime: &str,
    body: &[u8],
    head: bool,
    csp: &str,
    stopping: &AtomicBool,
) -> std::io::Result<()> {
    let header = format!("HTTP/1.1 {status}\r\nContent-Type: {mime}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: {csp}\r\nAllow: GET, HEAD\r\n\r\n", body.len());
    let deadline = Instant::now() + REQUEST_TIMEOUT;
    for part in std::iter::once(header.as_bytes()).chain(if head { None } else { Some(body) }) {
        let mut remaining = part;
        while !remaining.is_empty() {
            if stopping.load(Ordering::Acquire) || Instant::now() >= deadline {
                return Ok(());
            }
            match stream.write(remaining) {
                Ok(0) => return Ok(()),
                Ok(count) => remaining = &remaining[count..],
                Err(error)
                    if matches!(
                        error.kind(),
                        std::io::ErrorKind::WouldBlock
                            | std::io::ErrorKind::TimedOut
                            | std::io::ErrorKind::Interrupted
                    ) =>
                {
                    continue
                }
                Err(error) => return Err(error),
            }
        }
    }
    Ok(())
}

fn relative_path(value: &str) -> Option<PathBuf> {
    if value.is_empty()
        || value.contains(['\\', ':', '\0', '#'])
        || value.starts_with('/')
        || value.split('/').any(|part| {
            part.is_empty()
                || part == "."
                || part == ".."
                || part.ends_with([' ', '.'])
                || part.chars().any(char::is_control)
        })
    {
        return None;
    }
    let path = PathBuf::from(value);
    if path
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
    {
        return None;
    }
    Some(path)
}

fn checked_file(root: &Path, relative: &Path) -> Result<PathBuf, ()> {
    let mut path = root.to_path_buf();
    for component in relative.components() {
        if !matches!(component, Component::Normal(_)) {
            return Err(());
        }
        path.push(component);
        let metadata = fs::symlink_metadata(&path).map_err(|_| ())?;
        if metadata.file_type().is_symlink() {
            return Err(());
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Err(());
            }
        }
    }
    let canonical = path.canonicalize().map_err(|_| ())?;
    if !canonical.starts_with(root) || !canonical.is_file() {
        return Err(());
    }
    Ok(canonical)
}

fn decode_path(value: &str) -> Option<String> {
    let mut bytes = Vec::with_capacity(value.len());
    let input = value.as_bytes();
    let mut index = 0;
    while index < input.len() {
        if input[index] == b'%' {
            let high = (*input.get(index + 1)? as char).to_digit(16)?;
            let low = (*input.get(index + 2)? as char).to_digit(16)?;
            bytes.push((high * 16 + low) as u8);
            index += 3;
        } else {
            bytes.push(input[index]);
            index += 1;
        }
    }
    String::from_utf8(bytes).ok()
}

fn encode_path(value: &str) -> String {
    let mut result = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || b"/-._~".contains(&byte) {
            result.push(byte as char);
        } else {
            result.push_str(&format!("%{byte:02X}"));
        }
    }
    result
}

fn mime(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "map" => "application/json; charset=utf-8",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "svg" => "image/svg+xml",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "wasm" => "application/wasm",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn static_server_requires_nonce_host_and_get_then_releases_listener() {
        let root =
            std::env::temp_dir().join(format!("opendesk-static-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("index.html"), b"<p>module</p>").unwrap();
        let server = StaticServer::start(root.clone(), "index.html").unwrap();
        let url = tauri::Url::parse(server.url()).unwrap();
        let address = server.address;
        let send = |method: &str, path: &str, host: &str| {
            let mut client = TcpStream::connect(address).unwrap();
            client
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            write!(
                client,
                "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            )
            .unwrap();
            let mut reply = String::new();
            client.read_to_string(&mut reply).unwrap();
            reply
        };
        let success = send("GET", url.path(), &address.to_string());
        assert!(success.starts_with("HTTP/1.1 200"));
        assert!(success.ends_with("<p>module</p>"));
        assert!(success.contains("sandbox allow-scripts allow-same-origin allow-downloads"));
        assert!(!success.contains("Access-Control-Allow-Origin"));
        assert!(send("GET", "/index.html", &address.to_string()).starts_with("HTTP/1.1 404"));
        assert!(send("GET", url.path(), "attacker.example").starts_with("HTTP/1.1 403"));
        assert!(send("POST", url.path(), &address.to_string()).starts_with("HTTP/1.1 405"));
        let escaped = url.path().replace("index.html", "%2e%2e/secret");
        assert!(send("GET", &escaped, &address.to_string()).starts_with("HTTP/1.1 404"));
        drop(server);
        assert!(TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn paths_reject_windows_and_encoded_escape_forms() {
        for invalid in [
            "../secret",
            "/secret",
            "a/../b",
            "a\\b",
            "C:secret",
            "a//b",
            "a/./b",
            "a/file.",
            "a/file ",
            "a\0b",
        ] {
            assert!(relative_path(invalid).is_none(), "{invalid:?}");
        }
        assert!(relative_path(&decode_path("%2e%2e/secret").unwrap()).is_none());
        assert!(relative_path(&decode_path("a%5cb").unwrap()).is_none());
        assert!(decode_path("%ff").is_none());
        assert!(decode_path("%a").is_none());
        assert_eq!(
            decode_path(&encode_path("assets/中文 file.js")).unwrap(),
            "assets/中文 file.js"
        );
    }
}
