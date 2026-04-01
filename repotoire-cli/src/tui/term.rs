//! Terminal control: raw mode, alternate screen, cursor, terminal size.
//! Uses libc directly (Unix/POSIX) — no crossterm dependency.

use std::io::{self, Write};
use std::os::unix::io::AsRawFd;
use std::sync::{atomic::AtomicBool, atomic::Ordering, OnceLock};

static RAW_MODE_ACTIVE: AtomicBool = AtomicBool::new(false);
/// Saved original termios for full restoration in panic hook.
static ORIGINAL_TERMIOS: OnceLock<libc::termios> = OnceLock::new();

/// RAII guard that enters raw mode and restores original termios on drop.
/// Drop order matters: drop AltScreenGuard before this (Rust drops in reverse declaration order).
#[must_use = "raw mode is exited when the guard is dropped"]
pub struct RawModeGuard {
    fd: i32,
    original: libc::termios,
}

impl RawModeGuard {
    pub fn enter() -> io::Result<Self> {
        let fd = io::stdin().as_raw_fd();
        // SAFETY: zeroed termios is valid for tcgetattr to populate.
        let mut original: libc::termios = unsafe { std::mem::zeroed() };
        // SAFETY: fd is a valid file descriptor (stdin fd 0, always open for process lifetime).
        if unsafe { libc::tcgetattr(fd, &mut original) } != 0 {
            return Err(io::Error::last_os_error());
        }
        // Save original for panic hook to fully restore
        ORIGINAL_TERMIOS.set(original).ok();

        let mut raw = original;
        // SAFETY: raw is a valid termios struct copied from the original.
        unsafe { libc::cfmakeraw(&mut raw) };
        // VMIN=0, VTIME=1: read() returns after 100ms if no input (for ESC disambiguation)
        raw.c_cc[libc::VMIN] = 0;
        raw.c_cc[libc::VTIME] = 1;
        // SAFETY: fd is valid; raw is a properly initialized termios struct.
        if unsafe { libc::tcsetattr(fd, libc::TCSAFLUSH, &raw) } != 0 {
            return Err(io::Error::last_os_error());
        }
        RAW_MODE_ACTIVE.store(true, Ordering::SeqCst);
        Ok(Self { fd, original })
    }
}

impl Drop for RawModeGuard {
    fn drop(&mut self) {
        // SAFETY: fd 0 (stdin) remains valid for process lifetime; original was saved in enter().
        let ret = unsafe { libc::tcsetattr(self.fd, libc::TCSAFLUSH, &self.original) };
        // Only clear the flag if restoration succeeded
        if ret == 0 {
            RAW_MODE_ACTIVE.store(false, Ordering::SeqCst);
        }
    }
}

/// RAII guard that enters the alternate screen buffer and restores on drop.
#[must_use = "alternate screen is exited when the guard is dropped"]
pub struct AltScreenGuard;

impl AltScreenGuard {
    pub fn enter() -> io::Result<Self> {
        let mut out = io::stdout();
        out.write_all(b"\x1b[?1049h")?;
        out.flush()?;
        Ok(Self)
    }
}

impl Drop for AltScreenGuard {
    fn drop(&mut self) {
        let mut out = io::stdout();
        let _ = out.write_all(b"\x1b[?1049l");
        let _ = out.flush();
    }
}

/// Install a panic hook that restores terminal state before printing the panic.
pub fn install_panic_hook() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let mut out = io::stdout();
        // Restore alternate screen, show cursor, reset style
        let _ = out.write_all(b"\x1b[?1049l\x1b[?25h\x1b[0m");
        let _ = out.flush();
        // Fully restore original termios if available
        if RAW_MODE_ACTIVE.load(Ordering::SeqCst) {
            if let Some(original) = ORIGINAL_TERMIOS.get() {
                let fd = io::stdin().as_raw_fd();
                // SAFETY: fd 0 is valid; original is the saved pre-raw-mode termios.
                unsafe { libc::tcsetattr(fd, libc::TCSAFLUSH, original) };
            }
        }
        default_hook(info);
    }));
}

/// Get terminal dimensions (columns, rows).
pub fn terminal_size() -> io::Result<(u16, u16)> {
    // SAFETY: zeroed winsize is valid for ioctl to populate.
    let mut ws: libc::winsize = unsafe { std::mem::zeroed() };
    let fd = io::stdout().as_raw_fd();
    // SAFETY: fd is a valid stdout fd; ws is a valid winsize pointer; TIOCGWINSZ is a read-only ioctl.
    if unsafe { libc::ioctl(fd, libc::TIOCGWINSZ, &mut ws) } != 0 {
        return Err(io::Error::last_os_error());
    }
    if ws.ws_col == 0 || ws.ws_row == 0 {
        return Err(io::Error::other("terminal reported 0x0 dimensions"));
    }
    Ok((ws.ws_col, ws.ws_row))
}

/// Hide the terminal cursor.
pub fn hide_cursor() -> io::Result<()> {
    let mut out = io::stdout();
    out.write_all(b"\x1b[?25l")?;
    out.flush()
}

/// Show the terminal cursor.
pub fn show_cursor() -> io::Result<()> {
    let mut out = io::stdout();
    out.write_all(b"\x1b[?25h")?;
    out.flush()
}
