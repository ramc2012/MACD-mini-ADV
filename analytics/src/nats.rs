//! A minimal NATS subscriber: the text protocol needs a handful of verbs,
//! and the bus is plain TCP inside the Compose network.

use std::time::Duration;
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::TcpStream,
};

/// Parse `MSG <subject> <sid> [reply-to] <#bytes>` into (subject, size).
pub fn parse_msg_header(line: &str) -> Option<(&str, usize)> {
    let mut parts = line.split_ascii_whitespace();
    if parts.next()? != "MSG" {
        return None;
    }
    let subject = parts.next()?;
    let rest: Vec<&str> = parts.collect();
    if !(2..=3).contains(&rest.len()) {
        return None;
    }
    Some((subject, rest.last()?.parse().ok()?))
}

/// Subscribe and hand every message to `on_message` until the connection
/// fails; the caller reconnects. `on_ready` runs once the server has answered
/// the handshake's PING, i.e. it accepted CONNECT and SUB.
pub async fn subscribe<F, R>(address: &str, subject: &str, on_ready: R, mut on_message: F) -> Result<(), String>
where
    F: FnMut(&str, Vec<u8>),
    R: FnOnce(),
{
    let mut on_ready = Some(on_ready);
    let stream = tokio::time::timeout(Duration::from_secs(5), TcpStream::connect(address))
        .await
        .map_err(|_| "connect timed out".to_owned())?
        .map_err(|error| format!("connect: {error}"))?;
    stream.set_nodelay(true).ok();
    let (read, mut write) = stream.into_split();
    let mut reader = BufReader::with_capacity(1 << 20, read);
    let hello = format!(
        "CONNECT {{\"verbose\":false,\"pedantic\":false,\"name\":\"macd-analytics\",\"lang\":\"rust\",\"version\":\"0.1\",\"protocol\":1}}\r\nSUB {subject} 1\r\nPING\r\n"
    );
    write.write_all(hello.as_bytes()).await.map_err(|error| format!("handshake: {error}"))?;
    let mut line = String::new();
    loop {
        line.clear();
        if reader.read_line(&mut line).await.map_err(|error| format!("read: {error}"))? == 0 {
            return Err("server closed the connection".into());
        }
        let text = line.trim_end();
        if let Some((msg_subject, size)) = parse_msg_header(text) {
            let msg_subject = msg_subject.to_owned();
            let mut payload = vec![0u8; size + 2];
            reader.read_exact(&mut payload).await.map_err(|error| format!("payload: {error}"))?;
            payload.truncate(size);
            on_message(&msg_subject, payload);
        } else if text == "PONG" {
            if let Some(ready) = on_ready.take() {
                ready();
            }
        } else if text == "PING" {
            write.write_all(b"PONG\r\n").await.map_err(|error| format!("pong: {error}"))?;
        } else if let Some(error) = text.strip_prefix("-ERR") {
            return Err(format!("server error:{error}"));
        }
        // INFO and +OK need no action.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;

    #[test]
    fn message_headers_parse_with_and_without_reply_subjects() {
        assert_eq!(parse_msg_header("MSG md.tick.NSE:A 1 42"), Some(("md.tick.NSE:A", 42)));
        assert_eq!(parse_msg_header("MSG md.tick.NSE:A 1 _INBOX.x 7"), Some(("md.tick.NSE:A", 7)));
        assert_eq!(parse_msg_header("PING"), None);
        assert_eq!(parse_msg_header("MSG only"), None);
    }

    #[tokio::test]
    async fn subscribes_answers_pings_and_delivers_payloads() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap().to_string();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            socket.write_all(b"INFO {\"max_payload\":1048576}\r\n").await.unwrap();
            let mut seen = Vec::new();
            let mut buf = [0u8; 512];
            while !String::from_utf8_lossy(&seen).contains("PING\r\n") {
                let n = socket.read(&mut buf).await.unwrap();
                seen.extend_from_slice(&buf[..n]);
            }
            assert!(String::from_utf8_lossy(&seen).contains("SUB md.tick.> 1\r\n"));
            socket.write_all(b"PONG\r\nMSG md.tick.NSE:A 1 5\r\nhello\r\nPING\r\nMSG md.tick.NSE:B 1 0\r\n\r\n").await.unwrap();
            let mut reply = [0u8; 6];
            socket.read_exact(&mut reply).await.unwrap();
            assert_eq!(&reply, b"PONG\r\n");
            socket.write_all(b"-ERR 'test over'\r\n").await.unwrap();
        });
        let mut got = Vec::new();
        let mut ready = false;
        let result = subscribe(&address, "md.tick.>", || ready = true, |subject, payload| got.push((subject.to_owned(), payload))).await;
        server.await.unwrap();
        assert!(result.unwrap_err().contains("test over"));
        assert!(ready, "the handshake PONG marks the subscription ready");
        assert_eq!(got, vec![("md.tick.NSE:A".to_owned(), b"hello".to_vec()), ("md.tick.NSE:B".to_owned(), Vec::new())]);
    }
}
