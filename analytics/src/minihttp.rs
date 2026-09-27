//! A minimal HTTP/1.1 GET client for the few internal calls this service
//! makes (engine chart history and settings, QuestDB DDL).

use std::time::Duration;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
};

/// `http://host:port` (no path) to `host:port`.
pub fn host_port(origin: &str) -> Result<String, String> {
    let rest = origin.strip_prefix("http://").ok_or_else(|| format!("{origin}: only http:// is supported"))?;
    let host = rest.trim_end_matches('/');
    if host.is_empty() || host.contains('/') {
        return Err(format!("{origin}: expected an origin without a path"));
    }
    Ok(if host.contains(':') { host.to_owned() } else { format!("{host}:80") })
}

/// Percent-encode everything outside RFC 3986's unreserved set, plus `:`,
/// which symbol names use and paths allow.
pub fn encode(value: &str, keep_colon: bool) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => out.push(byte as char),
            b':' if keep_colon => out.push(':'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Decode a chunked body; `Ok(None)` means more bytes are still to come.
pub fn decode_chunked(mut body: &[u8]) -> Result<Option<Vec<u8>>, String> {
    let mut out = Vec::new();
    loop {
        let Some(end) = body.windows(2).position(|pair| pair == b"\r\n") else { return Ok(None) };
        let size_text = std::str::from_utf8(&body[..end]).map_err(|_| "chunk size")?;
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or("").trim(), 16).map_err(|_| "chunk size")?;
        body = &body[end + 2..];
        if size == 0 {
            return Ok(Some(out));
        }
        if body.len() < size + 2 {
            return Ok(None);
        }
        out.extend_from_slice(&body[..size]);
        body = &body[size + 2..];
    }
}

/// Parse a response once it is complete. Servers such as QuestDB keep the
/// connection open despite `Connection: close`, so completeness comes from
/// the framing, and end-of-stream is only needed for unframed bodies.
pub fn parse_response(raw: &[u8], eof: bool) -> Result<Option<(u16, Vec<u8>)>, String> {
    let Some(split) = raw.windows(4).position(|window| window == b"\r\n\r\n") else {
        return if eof { Err("no header terminator".into()) } else { Ok(None) };
    };
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| "header encoding")?;
    let body = &raw[split + 4..];
    let mut lines = head.split("\r\n");
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse().ok())
        .ok_or("status line")?;
    let mut chunked = false;
    let mut length = None;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else { continue };
        let value = value.trim();
        if name.eq_ignore_ascii_case("transfer-encoding") && value.eq_ignore_ascii_case("chunked") {
            chunked = true;
        } else if name.eq_ignore_ascii_case("content-length") {
            length = value.parse::<usize>().ok();
        }
    }
    let body = if chunked {
        decode_chunked(body)?
    } else if let Some(length) = length {
        body.get(..length).map(<[u8]>::to_vec)
    } else if eof {
        Some(body.to_vec())
    } else {
        None
    };
    match body {
        Some(body) => Ok(Some((status, body))),
        None if eof => Err("truncated body".into()),
        None => Ok(None),
    }
}

pub async fn get(address: &str, path: &str, headers: &[(&str, &str)], timeout: Duration) -> Result<(u16, Vec<u8>), String> {
    tokio::time::timeout(timeout, async {
        let mut stream = TcpStream::connect(address).await.map_err(|error| format!("connect {address}: {error}"))?;
        let mut request = format!("GET {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\nAccept-Encoding: identity\r\n");
        for (name, value) in headers {
            request.push_str(&format!("{name}: {value}\r\n"));
        }
        request.push_str("\r\n");
        stream.write_all(request.as_bytes()).await.map_err(|error| format!("send: {error}"))?;
        let mut raw = Vec::new();
        let mut buffer = [0u8; 16 << 10];
        loop {
            let read = stream.read(&mut buffer).await.map_err(|error| format!("receive: {error}"))?;
            raw.extend_from_slice(&buffer[..read]);
            if let Some(response) = parse_response(&raw, read == 0)? {
                return Ok(response);
            }
        }
    })
    .await
    .map_err(|_| format!("GET {path} timed out"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[test]
    fn origins_and_encoding() {
        assert_eq!(host_port("http://engine:8100").unwrap(), "engine:8100");
        assert_eq!(host_port("http://questdb").unwrap(), "questdb:80");
        assert!(host_port("https://x").is_err());
        assert_eq!(encode("NSE:M&M-EQ", true), "NSE:M%26M-EQ");
        assert_eq!(encode("a b=c", false), "a%20b%3Dc");
    }

    #[test]
    fn responses_with_length_chunks_or_eof() {
        let (status, body) = parse_response(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nokEXTRA", false).unwrap().unwrap();
        assert_eq!((status, body.as_slice()), (200, b"ok".as_slice()));
        let chunked = b"HTTP/1.1 404 Not Found\r\ntransfer-encoding: chunked\r\n\r\n4\r\nnot \r\n5;x=1\r\nfound\r\n0\r\n\r\n";
        let (status, body) = parse_response(chunked, false).unwrap().unwrap();
        assert_eq!((status, body.as_slice()), (404, b"not found".as_slice()));
        // Complete framing is recognised before the server closes (QuestDB keeps it open).
        assert_eq!(parse_response(&chunked[..chunked.len() - 5], false).unwrap(), None);
        assert_eq!(parse_response(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\npart", false).unwrap(), None);
        let (_, body) = parse_response(b"HTTP/1.0 200 OK\r\n\r\nuntil eof", true).unwrap().unwrap();
        assert_eq!(body, b"until eof");
        assert_eq!(parse_response(b"HTTP/1.0 200 OK\r\n\r\nuntil eof", false).unwrap(), None);
        assert!(parse_response(b"HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\nff\r\nshort", true).is_err());
    }

    #[tokio::test]
    async fn get_returns_without_waiting_for_a_kept_alive_connection() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap().to_string();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut request = [0u8; 1024];
            let _ = socket.read(&mut request).await.unwrap();
            socket.write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n").await.unwrap();
            tokio::time::sleep(Duration::from_secs(30)).await; // never closes
        });
        let (status, body) = get(&address, "/exec", &[], Duration::from_secs(2)).await.unwrap();
        assert_eq!((status, body.as_slice()), (200, b"ok".as_slice()));
        server.abort();
    }
}
