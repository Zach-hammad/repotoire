//! Keyboard input: poll for events, read and parse key sequences from stdin.
//! Handles escape sequence disambiguation via poll timeout.

use std::io::{self, Read};
use std::os::unix::io::AsRawFd;
use std::time::Duration;

/// A parsed key event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Key {
    Char(char),
    Enter,
    Escape,
    Backspace,
    Up,
    Down,
    Left,
    Right,
    PageUp,
    PageDown,
    Home,
    End,
    Delete,
    Tab,
}

/// Check if a key event is available within the given timeout.
pub fn poll_key(timeout: Duration) -> io::Result<bool> {
    let fd = io::stdin().as_raw_fd();
    let mut pfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    let millis = timeout.as_millis().min(i32::MAX as u128) as i32;
    // SAFETY: pfd is a valid pollfd struct; nfds=1 matches the single fd; millis is a valid timeout.
    let ret = unsafe { libc::poll(&mut pfd, 1, millis) };
    if ret < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(ret > 0 && (pfd.revents & libc::POLLIN) != 0)
    }
}

/// Read and parse the next key from stdin. Blocks briefly if needed.
pub fn read_key() -> io::Result<Key> {
    let mut buf = [0u8; 8];
    let n = io::stdin().read(&mut buf)?;
    if n == 0 {
        return Ok(Key::Escape); // EOF / timeout
    }
    Ok(parse_key(&buf[..n]))
}

fn parse_key(buf: &[u8]) -> Key {
    match buf[0] {
        b'\x1b' => {
            if buf.len() == 1 {
                return Key::Escape;
            }
            if buf.len() >= 3 && buf[1] == b'[' {
                return parse_csi(&buf[2..]);
            }
            if buf.len() >= 3 && buf[1] == b'O' {
                return parse_ss3(buf[2]);
            }
            Key::Escape
        }
        b'\r' | b'\n' => Key::Enter,
        b'\t' => Key::Tab,
        127 => Key::Backspace,
        // Ctrl+C — pass through as escape to allow quitting
        3 => Key::Escape,
        c if c < 32 => Key::Char((c + b'a' - 1) as char), // Ctrl+letter
        _ => {
            // UTF-8 character
            if let Ok(s) = std::str::from_utf8(buf) {
                if let Some(ch) = s.chars().next() {
                    return Key::Char(ch);
                }
            }
            Key::Char(buf[0] as char)
        }
    }
}

fn parse_csi(buf: &[u8]) -> Key {
    match buf.first() {
        Some(b'A') => Key::Up,
        Some(b'B') => Key::Down,
        Some(b'C') => Key::Right,
        Some(b'D') => Key::Left,
        Some(b'H') => Key::Home,
        Some(b'F') => Key::End,
        Some(b'5') if buf.get(1) == Some(&b'~') => Key::PageUp,
        Some(b'6') if buf.get(1) == Some(&b'~') => Key::PageDown,
        Some(b'3') if buf.get(1) == Some(&b'~') => Key::Delete,
        Some(b'1') if buf.get(1) == Some(&b'~') => Key::Home,
        Some(b'4') if buf.get(1) == Some(&b'~') => Key::End,
        _ => Key::Escape, // Unknown CSI sequence → treat as ESC
    }
}

fn parse_ss3(b: u8) -> Key {
    match b {
        b'A' => Key::Up,
        b'B' => Key::Down,
        b'C' => Key::Right,
        b'D' => Key::Left,
        b'H' => Key::Home,
        b'F' => Key::End,
        _ => Key::Escape,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_regular_chars() {
        assert_eq!(parse_key(b"a"), Key::Char('a'));
        assert_eq!(parse_key(b"Z"), Key::Char('Z'));
        assert_eq!(parse_key(b"1"), Key::Char('1'));
    }

    #[test]
    fn test_parse_control_keys() {
        assert_eq!(parse_key(b"\r"), Key::Enter);
        assert_eq!(parse_key(b"\t"), Key::Tab);
        assert_eq!(parse_key(&[127]), Key::Backspace);
    }

    #[test]
    fn test_parse_escape() {
        assert_eq!(parse_key(b"\x1b"), Key::Escape);
    }

    #[test]
    fn test_parse_arrow_keys() {
        assert_eq!(parse_key(b"\x1b[A"), Key::Up);
        assert_eq!(parse_key(b"\x1b[B"), Key::Down);
        assert_eq!(parse_key(b"\x1b[C"), Key::Right);
        assert_eq!(parse_key(b"\x1b[D"), Key::Left);
    }

    #[test]
    fn test_parse_page_keys() {
        assert_eq!(parse_key(b"\x1b[5~"), Key::PageUp);
        assert_eq!(parse_key(b"\x1b[6~"), Key::PageDown);
    }

    #[test]
    fn test_parse_home_end() {
        assert_eq!(parse_key(b"\x1b[H"), Key::Home);
        assert_eq!(parse_key(b"\x1b[F"), Key::End);
    }
}
