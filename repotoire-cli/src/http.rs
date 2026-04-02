//! Minimal HTTP/1.1 client over TcpStream + rustls.
//! Replaces ureq (~41 transitive deps) for 4 call sites.
//! Supports both http:// (Ollama localhost) and https:// (cloud APIs).

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use anyhow::{bail, Context, Result};

// ============================================================================
// PUBLIC API
// ============================================================================

/// HTTP response: status code + body as string.
pub struct HttpResponse {
    pub status: u16,
    pub body: String,
}

/// HTTP GET request. Returns response with status and body.
pub fn get(url: &str, timeout: Duration) -> Result<HttpResponse> {
    let parsed = parse_url(url)?;
    let mut stream = connect(&parsed, timeout)?;

    write!(
        stream,
        "GET {} HTTP/1.1\r\nHost: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n",
        parsed.path, parsed.host
    )?;
    stream.flush()?;

    read_response(&mut *stream)
}

/// HTTP POST with JSON body and custom headers.
/// Content-Type: application/json is sent automatically — callers pass only extra headers
/// (e.g. Authorization, x-api-key).
pub fn post_json(
    url: &str,
    headers: &[(&str, &str)],
    body: &str,
    timeout: Duration,
) -> Result<HttpResponse> {
    let parsed = parse_url(url)?;
    let mut stream = connect(&parsed, timeout)?;

    write!(
        stream,
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n",
        parsed.path, parsed.host, body.len()
    )?;
    for (name, value) in headers {
        write!(stream, "{}: {}\r\n", name, value)?;
    }
    write!(stream, "\r\n{}", body)?;
    stream.flush()?;

    read_response(&mut *stream)
}

// ============================================================================
// URL PARSING
// ============================================================================

struct Url<'a> {
    scheme: &'a str,
    host: &'a str,
    port: u16,
    path: &'a str,
}

fn parse_url(url: &str) -> Result<Url<'_>> {
    let (scheme, rest) = url
        .split_once("://")
        .context("URL must start with http:// or https://")?;
    let (authority, path) = match rest.find('/') {
        Some(idx) => (&rest[..idx], &rest[idx..]),
        None => (rest, "/"),
    };
    let (host, port) = if let Some((h, p)) = authority.split_once(':') {
        (h, p.parse::<u16>().context("invalid port")?)
    } else {
        let default_port = if scheme == "https" { 443 } else { 80 };
        (authority, default_port)
    };
    Ok(Url {
        scheme,
        host,
        port,
        path,
    })
}

// ============================================================================
// CONNECTION (TCP + optional TLS)
// ============================================================================

trait ReadWrite: Read + Write {}
impl<T: Read + Write> ReadWrite for T {}

static TLS_CONFIG: OnceLock<Arc<rustls::ClientConfig>> = OnceLock::new();

fn tls_config() -> Arc<rustls::ClientConfig> {
    TLS_CONFIG
        .get_or_init(|| {
            let root_store = rustls::RootCertStore::from_iter(
                webpki_roots::TLS_SERVER_ROOTS.iter().cloned(),
            );
            Arc::new(
                rustls::ClientConfig::builder()
                    .with_root_certificates(root_store)
                    .with_no_client_auth(),
            )
        })
        .clone()
}

fn connect(url: &Url, timeout: Duration) -> Result<Box<dyn ReadWrite>> {
    let addr = format!("{}:{}", url.host, url.port);
    let stream =
        TcpStream::connect(&addr).with_context(|| format!("failed to connect to {}", addr))?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;

    if url.scheme == "https" {
        let server_name = rustls::pki_types::ServerName::try_from(url.host.to_string())
            .map_err(|e| anyhow::anyhow!("invalid server name: {}", e))?;
        let conn = rustls::ClientConnection::new(tls_config(), server_name)
            .context("TLS connection failed")?;
        Ok(Box::new(rustls::StreamOwned::new(conn, stream)))
    } else {
        Ok(Box::new(stream))
    }
}

// ============================================================================
// RESPONSE READING
// ============================================================================

fn read_response(stream: &mut dyn ReadWrite) -> Result<HttpResponse> {
    let mut buf = Vec::with_capacity(4096);
    let mut byte = [0u8; 1];

    // Read until \r\n\r\n (end of headers)
    loop {
        stream.read_exact(&mut byte)?;
        buf.push(byte[0]);
        if buf.ends_with(b"\r\n\r\n") {
            break;
        }
        if buf.len() > 65536 {
            bail!("response headers too large (>64KB)");
        }
    }

    let header_str = String::from_utf8_lossy(&buf);
    let mut lines = header_str.lines();

    // Parse status line: "HTTP/1.1 200 OK"
    let status_line = lines.next().context("empty HTTP response")?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .context("missing HTTP status code")?
        .parse::<u16>()
        .context("invalid HTTP status code")?;

    // Parse headers (case-insensitive)
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for line in lines {
        if line.is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            let name = name.trim().to_ascii_lowercase();
            let value = value.trim();
            match name.as_str() {
                "content-length" => content_length = value.parse().ok(),
                "transfer-encoding" => {
                    chunked = value.to_ascii_lowercase().contains("chunked")
                }
                _ => {}
            }
        }
    }

    // Read body
    let body = if chunked {
        read_chunked(stream)?
    } else if let Some(len) = content_length {
        let mut body = vec![0u8; len];
        stream.read_exact(&mut body)?;
        String::from_utf8_lossy(&body).into_owned()
    } else {
        // No Content-Length, no chunked — read to EOF (Connection: close)
        let mut body = Vec::new();
        stream.read_to_end(&mut body)?;
        String::from_utf8_lossy(&body).into_owned()
    };

    Ok(HttpResponse { status, body })
}

fn read_chunked(stream: &mut dyn ReadWrite) -> Result<String> {
    let mut body = Vec::new();
    loop {
        // Read chunk size line (hex digits terminated by \r\n)
        let mut size_line = Vec::new();
        let mut byte = [0u8; 1];
        loop {
            stream.read_exact(&mut byte)?;
            if byte[0] == b'\n' {
                break;
            }
            if byte[0] != b'\r' {
                size_line.push(byte[0]);
            }
        }
        let size_str = std::str::from_utf8(&size_line).context("invalid chunk size encoding")?;
        let size =
            usize::from_str_radix(size_str.trim(), 16).context("invalid chunk size hex")?;
        if size == 0 {
            break;
        }

        // Read chunk data + trailing \r\n
        let mut chunk = vec![0u8; size];
        stream.read_exact(&mut chunk)?;
        body.extend_from_slice(&chunk);

        let mut crlf = [0u8; 2];
        stream.read_exact(&mut crlf)?;
    }
    // Consume trailing \r\n after final 0-size chunk
    let mut byte = [0u8; 1];
    loop {
        stream.read_exact(&mut byte)?;
        if byte[0] == b'\n' {
            break;
        }
    }
    Ok(String::from_utf8_lossy(&body).into_owned())
}

// ============================================================================
// TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_url_https() {
        let u = parse_url("https://api.example.com/v1/endpoint").expect("parse");
        assert_eq!(u.scheme, "https");
        assert_eq!(u.host, "api.example.com");
        assert_eq!(u.port, 443);
        assert_eq!(u.path, "/v1/endpoint");
    }

    #[test]
    fn test_parse_url_http_with_port() {
        let u = parse_url("http://localhost:11434/v1/chat").expect("parse");
        assert_eq!(u.scheme, "http");
        assert_eq!(u.host, "localhost");
        assert_eq!(u.port, 11434);
        assert_eq!(u.path, "/v1/chat");
    }

    #[test]
    fn test_parse_url_no_path() {
        let u = parse_url("https://example.com").expect("parse");
        assert_eq!(u.path, "/");
        assert_eq!(u.port, 443);
    }

    #[test]
    fn test_parse_url_http_default_port() {
        let u = parse_url("http://example.com/test").expect("parse");
        assert_eq!(u.port, 80);
        assert_eq!(u.scheme, "http");
    }

    #[test]
    fn test_parse_url_invalid() {
        assert!(parse_url("not-a-url").is_err());
    }

    #[test]
    fn test_parse_url_with_query_string() {
        let u = parse_url("https://api.example.com/v1/query?foo=bar&baz=1").expect("parse");
        assert_eq!(u.host, "api.example.com");
        assert_eq!(u.path, "/v1/query?foo=bar&baz=1");
    }

    #[test]
    fn test_read_chunked() {
        // Simulate a chunked response body
        let chunked_data = b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n";
        let mut cursor = std::io::Cursor::new(chunked_data.to_vec());
        let result = read_chunked(&mut cursor).expect("decode");
        assert_eq!(result, "hello world");
    }

    #[test]
    fn test_read_chunked_single() {
        let chunked_data = b"d\r\nHello, World!\r\n0\r\n\r\n";
        let mut cursor = std::io::Cursor::new(chunked_data.to_vec());
        let result = read_chunked(&mut cursor).expect("decode");
        assert_eq!(result, "Hello, World!");
    }

    #[test]
    fn test_read_response_content_length() {
        let response = b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\nHello, World!";
        let mut cursor = std::io::Cursor::new(response.to_vec());
        let resp = read_response(&mut cursor).expect("parse");
        assert_eq!(resp.status, 200);
        assert_eq!(resp.body, "Hello, World!");
    }

    #[test]
    fn test_read_response_chunked() {
        let response =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n";
        let mut cursor = std::io::Cursor::new(response.to_vec());
        let resp = read_response(&mut cursor).expect("parse");
        assert_eq!(resp.status, 200);
        assert_eq!(resp.body, "hello");
    }

    #[test]
    fn test_read_response_case_insensitive_headers() {
        let response = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nOK";
        let mut cursor = std::io::Cursor::new(response.to_vec());
        let resp = read_response(&mut cursor).expect("parse");
        assert_eq!(resp.status, 200);
        assert_eq!(resp.body, "OK");
    }

    #[test]
    fn test_read_response_404() {
        let response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found";
        let mut cursor = std::io::Cursor::new(response.to_vec());
        let resp = read_response(&mut cursor).expect("parse");
        assert_eq!(resp.status, 404);
        assert_eq!(resp.body, "Not Found");
    }
}
