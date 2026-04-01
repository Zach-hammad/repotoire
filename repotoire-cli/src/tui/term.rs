//! Terminal control: raw mode, alternate screen, cursor, terminal size.
//! Uses libc directly — no crossterm dependency.

use std::io::{self, Write};
use std::os::unix::io::AsRawFd;
use std::sync::atomic::{AtomicBool, Ordering};

static RAW_MODE_ACTIVE: AtomicBool = AtomicBool::new(false);

/// RAII guard that enters raw mode and restores original termios on drop.
pub struct RawModeGuard {
    fd: i32,
    original: libc::termios,
}

impl RawModeGuard {
    pub fn enter() -> io::Result<Self> {
        let fd = io::stdin().as_raw_fd();
        // SAFETY: zeroed termios is valid for tcgetattr to populate.
        let mut original: libc::termios = unsafe { std::mem::zeroed() };
        // SAFETY: fd is a valid file descriptor from stdin; original is a valid termios pointer.
        if unsafe { libc::tcgetattr(fd, &mut original) } != 0 {
            return Err(io::Error::last_os_error());
        }
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
        // SAFETY: fd and original were saved in enter() and are still valid.
        unsafe { libc::tcsetattr(self.fd, libc::TCSAFLUSH, &self.original) };
        RAW_MODE_ACTIVE.store(false, Ordering::SeqCst);
    }
}

/// RAII guard that enters the alternate screen buffer and restores on drop.
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
        // Restore terminal mode if still in raw mode
        if RAW_MODE_ACTIVE.load(Ordering::SeqCst) {
            let fd = io::stdin().as_raw_fd();
            // SAFETY: zeroed termios is valid for tcgetattr to populate.
            let mut termios: libc::termios = unsafe { std::mem::zeroed() };
            // SAFETY: fd is valid stdin fd; termios is a valid pointer.
            if unsafe { libc::tcgetattr(fd, &mut termios) } == 0 {
                // Re-enable canonical mode, echo, signals
                termios.c_lflag |= libc::ECHO | libc::ICANON | libc::ISIG;
                // SAFETY: fd is valid; termios has been read and modified safely.
                unsafe { libc::tcsetattr(fd, libc::TCSAFLUSH, &termios) };
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
    Ok((ws.ws_col, ws.ws_row))
}

/// Hide the terminal cursor.
pub fn hide_cursor() -> io::Result<()> {
    io::stdout().write_all(b"\x1b[?25l")?;
    io::stdout().flush()
}

/// Show the terminal cursor.
pub fn show_cursor() -> io::Result<()> {
    io::stdout().write_all(b"\x1b[?25h")?;
    io::stdout().flush()
}
