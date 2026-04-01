//! Double-buffered terminal rendering: Cell, Color, Style, Buffer, Screen, Rect, Constraint.
//! Diff-based flush writes only changed cells to minimize flicker and I/O.

use std::io::{self, BufWriter, Write};

// ============================================================================
// COLOR + STYLE
// ============================================================================

/// Terminal colors (16-color ANSI).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Color {
    Reset,
    Red,
    Green,
    Yellow,
    Blue,
    Magenta,
    Cyan,
    White,
    DarkGray,
}

impl Color {
    /// ANSI SGR foreground code.
    pub fn fg_code(self) -> &'static str {
        match self {
            Color::Reset => "39",
            Color::Red => "31",
            Color::Green => "32",
            Color::Yellow => "33",
            Color::Blue => "34",
            Color::Magenta => "35",
            Color::Cyan => "36",
            Color::White => "37",
            Color::DarkGray => "90",
        }
    }

    /// ANSI SGR background code.
    pub fn bg_code(self) -> &'static str {
        match self {
            Color::Reset => "49",
            Color::Red => "41",
            Color::Green => "42",
            Color::Yellow => "43",
            Color::Blue => "44",
            Color::Magenta => "45",
            Color::Cyan => "46",
            Color::White => "47",
            Color::DarkGray => "100",
        }
    }
}

/// Text style: foreground color, background color, bold.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Style {
    pub fg: Color,
    pub bg: Color,
    pub bold: bool,
}

impl Default for Style {
    fn default() -> Self {
        Self {
            fg: Color::Reset,
            bg: Color::Reset,
            bold: false,
        }
    }
}

impl Style {
    pub fn fg(mut self, color: Color) -> Self {
        self.fg = color;
        self
    }

    pub fn bg(mut self, color: Color) -> Self {
        self.bg = color;
        self
    }

    pub fn bold(mut self) -> Self {
        self.bold = true;
        self
    }
}

// ============================================================================
// CELL + BUFFER
// ============================================================================

/// A single terminal cell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cell {
    pub ch: char,
    pub style: Style,
}

impl Default for Cell {
    fn default() -> Self {
        Self {
            ch: ' ',
            style: Style::default(),
        }
    }
}

/// A 2D grid of cells.
pub struct Buffer {
    pub width: u16,
    pub height: u16,
    cells: Vec<Cell>,
}

impl Buffer {
    pub fn new(width: u16, height: u16) -> Self {
        Self {
            width,
            height,
            cells: vec![Cell::default(); (width as usize) * (height as usize)],
        }
    }

    pub fn clear(&mut self) {
        self.cells.fill(Cell::default());
    }

    pub fn resize(&mut self, width: u16, height: u16) {
        self.width = width;
        self.height = height;
        self.cells
            .resize((width as usize) * (height as usize), Cell::default());
        self.clear();
    }

    #[inline]
    fn idx(&self, x: u16, y: u16) -> usize {
        (y as usize) * (self.width as usize) + (x as usize)
    }

    /// Set a single cell.
    pub fn set(&mut self, x: u16, y: u16, ch: char, style: Style) {
        if x < self.width && y < self.height {
            let i = self.idx(x, y);
            self.cells[i] = Cell { ch, style };
        }
    }

    /// Write a string at (x, y) with the given style. Truncates at buffer width.
    pub fn set_str(&mut self, x: u16, y: u16, s: &str, style: Style) {
        self.set_str_max(x, y, s, style, self.width);
    }

    /// Write a string at (x, y), truncating at `max_x` (exclusive).
    /// Use this when rendering inside a bordered rect to avoid overwriting borders.
    pub fn set_str_max(&mut self, x: u16, y: u16, s: &str, style: Style, max_x: u16) {
        let limit = max_x.min(self.width);
        let mut col = x;
        for ch in s.chars() {
            if col >= limit {
                break;
            }
            if y < self.height {
                let i = self.idx(col, y);
                self.cells[i] = Cell { ch, style };
            }
            col += 1;
        }
    }

    /// Fill a rectangular region with a character and style.
    pub fn fill(&mut self, rect: Rect, ch: char, style: Style) {
        for y in rect.y..rect.y.saturating_add(rect.height).min(self.height) {
            for x in rect.x..rect.x.saturating_add(rect.width).min(self.width) {
                let i = self.idx(x, y);
                self.cells[i] = Cell { ch, style };
            }
        }
    }

    /// Draw a border (box-drawing chars) around a Rect. Returns inner Rect.
    pub fn draw_border(&mut self, rect: Rect, title: &str, style: Style) -> Rect {
        if rect.width < 2 || rect.height < 2 {
            return rect;
        }

        let x1 = rect.x;
        let y1 = rect.y;
        let x2 = rect.x + rect.width - 1;
        let y2 = rect.y + rect.height - 1;

        // Corners
        self.set(x1, y1, '\u{250c}', style); // top-left ┌
        self.set(x2, y1, '\u{2510}', style); // top-right ┐
        self.set(x1, y2, '\u{2514}', style); // bottom-left └
        self.set(x2, y2, '\u{2518}', style); // bottom-right ┘

        // Horizontal edges
        for x in (x1 + 1)..x2 {
            self.set(x, y1, '\u{2500}', style); // ─
            self.set(x, y2, '\u{2500}', style);
        }

        // Vertical edges
        for y in (y1 + 1)..y2 {
            self.set(x1, y, '\u{2502}', style); // │
            self.set(x2, y, '\u{2502}', style);
        }

        // Title (inside top border)
        if !title.is_empty() && rect.width > 4 {
            let max_title = (rect.width - 4) as usize;
            let display_title = if title.len() > max_title {
                // Find a valid UTF-8 boundary at or before max_title bytes
                let mut end = max_title;
                while end > 0 && !title.is_char_boundary(end) {
                    end -= 1;
                }
                &title[..end]
            } else {
                title
            };
            self.set_str(x1 + 2, y1, display_title, style);
        }

        // Inner rect
        Rect {
            x: rect.x + 1,
            y: rect.y + 1,
            width: rect.width.saturating_sub(2),
            height: rect.height.saturating_sub(2),
        }
    }
}

// ============================================================================
// RECT + CONSTRAINT + LAYOUT
// ============================================================================

/// A rectangle on screen.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rect {
    pub x: u16,
    pub y: u16,
    pub width: u16,
    pub height: u16,
}

impl Rect {
    pub fn new(x: u16, y: u16, width: u16, height: u16) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }
}

/// Layout constraint for splitting a Rect.
#[derive(Debug, Clone, Copy)]
pub enum Constraint {
    /// Fixed number of rows/columns.
    Length(u16),
    /// Minimum rows/columns, takes remaining space.
    Min(u16),
    /// Percentage of total.
    Percentage(u16),
}

/// Split a rect vertically (top to bottom) according to constraints.
pub fn split_vertical(rect: Rect, constraints: &[Constraint]) -> Vec<Rect> {
    let sizes = solve_constraints(rect.height, constraints);
    let mut result = Vec::with_capacity(constraints.len());
    let mut y = rect.y;
    for h in sizes {
        result.push(Rect {
            x: rect.x,
            y,
            width: rect.width,
            height: h,
        });
        y += h;
    }
    result
}

/// Split a rect horizontally (left to right) according to constraints.
pub fn split_horizontal(rect: Rect, constraints: &[Constraint]) -> Vec<Rect> {
    let sizes = solve_constraints(rect.width, constraints);
    let mut result = Vec::with_capacity(constraints.len());
    let mut x = rect.x;
    for w in sizes {
        result.push(Rect {
            x,
            y: rect.y,
            width: w,
            height: rect.height,
        });
        x += w;
    }
    result
}

fn solve_constraints(total: u16, constraints: &[Constraint]) -> Vec<u16> {
    let mut sizes: Vec<u16> = vec![0; constraints.len()];
    let mut remaining = total;
    let mut min_indices = Vec::new();

    // First pass: allocate fixed (Length) and percentage
    for (i, c) in constraints.iter().enumerate() {
        match c {
            Constraint::Length(n) => {
                let alloc = (*n).min(remaining);
                sizes[i] = alloc;
                remaining = remaining.saturating_sub(alloc);
            }
            Constraint::Percentage(p) => {
                let alloc = ((total as u32 * *p as u32) / 100) as u16;
                let alloc = alloc.min(remaining);
                sizes[i] = alloc;
                remaining = remaining.saturating_sub(alloc);
            }
            Constraint::Min(min) => {
                sizes[i] = (*min).min(remaining);
                remaining = remaining.saturating_sub(sizes[i]);
                min_indices.push(i);
            }
        }
    }

    // Second pass: distribute remaining space to Min constraints
    if !min_indices.is_empty() && remaining > 0 {
        let share = remaining / min_indices.len() as u16;
        let extra = remaining % min_indices.len() as u16;
        for (j, &i) in min_indices.iter().enumerate() {
            sizes[i] += share + if (j as u16) < extra { 1 } else { 0 };
        }
    }

    sizes
}

// ============================================================================
// SCREEN (double-buffered terminal output)
// ============================================================================

/// Double-buffered screen. Renders into `current`, diffs against `previous`, flushes changes.
pub struct Screen {
    pub current: Buffer,
    previous: Buffer,
    pub width: u16,
    pub height: u16,
}

impl Screen {
    pub fn new(width: u16, height: u16) -> Self {
        Self {
            current: Buffer::new(width, height),
            previous: Buffer::new(width, height),
            width,
            height,
        }
    }

    /// Get the full-screen Rect.
    pub fn area(&self) -> Rect {
        Rect::new(0, 0, self.width, self.height)
    }

    /// Resize both buffers. Clears the physical terminal to avoid stale artifacts.
    pub fn resize(&mut self, width: u16, height: u16) {
        self.width = width;
        self.height = height;
        self.current.resize(width, height);
        self.previous.resize(width, height);
        // Clear the physical terminal — the diff engine only writes changed cells,
        // so old content at the previous dimensions would persist without this.
        let _ = io::stdout().write_all(b"\x1b[2J");
        let _ = io::stdout().flush();
    }

    /// Clear the current buffer for a new frame.
    pub fn begin_frame(&mut self) {
        // Re-check terminal size each frame
        if let Ok((w, h)) = super::term::terminal_size() {
            if w != self.width || h != self.height {
                self.resize(w, h);
            }
        }
        self.current.clear();
    }

    /// Diff current vs previous, flush only changed cells to stdout, then swap.
    pub fn end_frame(&mut self) -> io::Result<()> {
        let mut out = BufWriter::new(io::stdout());
        let mut last_style: Option<Style> = None;

        for y in 0..self.height {
            for x in 0..self.width {
                let idx = (y as usize) * (self.width as usize) + (x as usize);
                if self.current.cells[idx] != self.previous.cells[idx] {
                    let cell = &self.current.cells[idx];

                    // Move cursor
                    write!(out, "\x1b[{};{}H", y + 1, x + 1)?;

                    // Emit style if changed
                    if last_style.as_ref() != Some(&cell.style) {
                        write!(
                            out,
                            "\x1b[0;{}{};{}m",
                            if cell.style.bold { "1;" } else { "" },
                            cell.style.fg.fg_code(),
                            cell.style.bg.bg_code(),
                        )?;
                        last_style = Some(cell.style);
                    }

                    write!(out, "{}", cell.ch)?;
                }
            }
        }

        // Reset style at end of frame
        write!(out, "\x1b[0m")?;
        out.flush()?;

        // Swap: previous = current (clone)
        std::mem::swap(&mut self.previous, &mut self.current);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_solve_constraints_fixed() {
        let sizes = solve_constraints(100, &[Constraint::Length(30), Constraint::Length(70)]);
        assert_eq!(sizes, vec![30, 70]);
    }

    #[test]
    fn test_solve_constraints_percentage() {
        let sizes = solve_constraints(
            100,
            &[Constraint::Percentage(40), Constraint::Percentage(60)],
        );
        assert_eq!(sizes, vec![40, 60]);
    }

    #[test]
    fn test_solve_constraints_min() {
        let sizes = solve_constraints(
            100,
            &[
                Constraint::Length(10),
                Constraint::Min(0),
                Constraint::Length(10),
            ],
        );
        assert_eq!(sizes, vec![10, 80, 10]);
    }

    #[test]
    fn test_solve_constraints_overflow() {
        let sizes = solve_constraints(10, &[Constraint::Length(20), Constraint::Length(20)]);
        assert_eq!(sizes, vec![10, 0]);
    }

    #[test]
    fn test_buffer_set_str() {
        let mut buf = Buffer::new(10, 1);
        buf.set_str(0, 0, "hello", Style::default());
        assert_eq!(buf.cells[0].ch, 'h');
        assert_eq!(buf.cells[4].ch, 'o');
        assert_eq!(buf.cells[5].ch, ' '); // untouched
    }

    #[test]
    fn test_buffer_set_str_truncates() {
        let mut buf = Buffer::new(3, 1);
        buf.set_str(0, 0, "hello", Style::default());
        assert_eq!(buf.cells[0].ch, 'h');
        assert_eq!(buf.cells[1].ch, 'e');
        assert_eq!(buf.cells[2].ch, 'l');
    }

    #[test]
    fn test_split_vertical() {
        let rect = Rect::new(0, 0, 80, 24);
        let chunks = split_vertical(
            rect,
            &[Constraint::Length(3), Constraint::Min(0), Constraint::Length(1)],
        );
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0].height, 3);
        assert_eq!(chunks[1].height, 20);
        assert_eq!(chunks[2].height, 1);
    }

    #[test]
    fn test_draw_border() {
        let mut buf = Buffer::new(10, 5);
        let inner = buf.draw_border(
            Rect::new(0, 0, 10, 5),
            " Test ",
            Style::default(),
        );
        assert_eq!(inner, Rect { x: 1, y: 1, width: 8, height: 3 });
        assert_eq!(buf.cells[0].ch, '\u{250c}'); // ┌
        assert_eq!(buf.cells[9].ch, '\u{2510}'); // ┐
    }

}
