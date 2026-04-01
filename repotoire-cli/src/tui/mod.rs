//! Minimal TUI framework for the findings browser.
//! Replaces ratatui + crossterm (~100 transitive deps) with ~400 lines + libc.

pub mod buffer;
pub mod input;
pub mod term;

pub use buffer::{
    split_horizontal, split_vertical, Buffer, Cell, Color, Constraint, Rect, Screen, Style,
};
pub use input::{poll_key, read_key, Key};
pub use term::{hide_cursor, install_panic_hook, show_cursor, AltScreenGuard, RawModeGuard};
