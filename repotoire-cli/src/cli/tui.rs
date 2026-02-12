//! Interactive TUI for browsing findings
//!
//! Keys:
//! - j/k or Up/Down: Navigate findings
//! - Enter: Toggle detail view
//! - f: Fix current finding with AI
//! - F: Launch agent to fix + create PR
//! - q/Esc: Quit

use anyhow::Result;
use crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    prelude::*,
    widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap},
};
use std::fs;
use std::io;
use std::path::Path;
use std::process::Command;

use crate::models::{Finding, Severity};

/// Read code snippet from file around the given line range
fn read_code_snippet(repo_path: &Path, file_path: &str, line_start: u32, line_end: u32, context: usize) -> Option<Vec<(u32, String)>> {
    let full_path = repo_path.join(file_path);
    let content = fs::read_to_string(&full_path).ok()?;
    let lines: Vec<&str> = content.lines().collect();
    
    let start = (line_start as usize).saturating_sub(context + 1);
    let end = (line_end as usize + context).min(lines.len());
    
    Some(
        lines[start..end]
            .iter()
            .enumerate()
            .map(|(i, line)| ((start + i + 1) as u32, line.to_string()))
            .collect()
    )
}

pub struct App {
    findings: Vec<Finding>,
    list_state: ListState,
    show_detail: bool,
    repo_path: std::path::PathBuf,
    code_cache: Option<Vec<(u32, String)>>,
    cached_index: Option<usize>,
    status_message: Option<(String, bool)>, // (message, is_error)
}

impl App {
    pub fn new(findings: Vec<Finding>, repo_path: std::path::PathBuf) -> Self {
        let mut list_state = ListState::default();
        if !findings.is_empty() {
            list_state.select(Some(0));
        }
        Self {
            findings,
            list_state,
            show_detail: false,
            repo_path,
            code_cache: None,
            cached_index: None,
            status_message: None,
        }
    }
    
    /// Launch Claude Code agent to fix the current finding and create a PR
    fn launch_agent(&mut self) -> Option<String> {
        let finding = self.selected_finding()?;
        let index = self.list_state.selected()? + 1;
        
        let file = finding.affected_files.first()
            .map(|p| p.display().to_string())
            .unwrap_or_default();
        
        let line_info = finding.line_start
            .map(|l| format!(":{}", l))
            .unwrap_or_default();
        
        // Create a task description for the agent
        let task = format!(
            "Fix this code issue and create a PR:\n\n\
             **Finding:** {}\n\
             **Severity:** {:?}\n\
             **File:** {}{}\n\
             **Description:** {}\n\n\
             Steps:\n\
             1. Create a new branch: fix/finding-{}\n\
             2. Fix the issue in {}\n\
             3. Commit with a descriptive message\n\
             4. Push and create a PR\n\n\
             {}",
            finding.title,
            finding.severity,
            file,
            line_info,
            finding.description,
            index,
            file,
            finding.suggested_fix.as_deref().unwrap_or("Apply the appropriate fix based on the description.")
        );
        
        // Try to spawn Claude Code in background
        let result = Command::new("claude")
            .args(["--print", "--dangerously-skip-permissions", &task])
            .current_dir(&self.repo_path)
            .spawn();
        
        match result {
            Ok(child) => {
                Some(format!("🚀 Agent launched (PID: {}) - fixing finding #{}", child.id(), index))
            }
            Err(e) => {
                // Try openclaw coding-agent as fallback
                if let Ok(child) = Command::new("openclaw")
                    .args(["coding-agent", "claude", "--", "--print", &task])
                    .current_dir(&self.repo_path)
                    .spawn()
                {
                    Some(format!("🚀 Agent launched via OpenClaw (PID: {})", child.id()))
                } else {
                    Some(format!("❌ Failed to launch agent: {}. Install Claude Code: npm i -g @anthropic-ai/claude-code", e))
                }
            }
        }
    }
    
    /// Run the built-in fix command for the current finding
    fn run_fix(&self) -> Option<String> {
        let index = self.list_state.selected()? + 1;
        
        // We can't run fix interactively from TUI, so just show the command
        Some(format!("Run: repotoire fix {}", index))
    }

    fn get_code_snippet(&mut self) -> Option<&Vec<(u32, String)>> {
        let selected = self.list_state.selected()?;
        
        // Return cached if same selection
        if self.cached_index == Some(selected) {
            return self.code_cache.as_ref();
        }
        
        let finding = self.findings.get(selected)?;
        let file_path = finding.affected_files.first()?;
        let line_start = finding.line_start.unwrap_or(1);
        let line_end = finding.line_end.unwrap_or(line_start);
        
        self.code_cache = read_code_snippet(
            &self.repo_path,
            &file_path.to_string_lossy(),
            line_start,
            line_end,
            3, // 3 lines of context
        );
        self.cached_index = Some(selected);
        self.code_cache.as_ref()
    }

    fn next(&mut self) {
        if self.findings.is_empty() {
            return;
        }
        let i = match self.list_state.selected() {
            Some(i) => (i + 1) % self.findings.len(),
            None => 0,
        };
        self.list_state.select(Some(i));
    }

    fn previous(&mut self) {
        if self.findings.is_empty() {
            return;
        }
        let i = match self.list_state.selected() {
            Some(i) => {
                if i == 0 {
                    self.findings.len() - 1
                } else {
                    i - 1
                }
            }
            None => 0,
        };
        self.list_state.select(Some(i));
    }

    fn selected_finding(&self) -> Option<&Finding> {
        self.list_state.selected().and_then(|i| self.findings.get(i))
    }
}

pub fn run(findings: Vec<Finding>, repo_path: std::path::PathBuf) -> Result<()> {
    // Check if we have a TTY
    use std::io::IsTerminal;
    if !io::stdout().is_terminal() {
        anyhow::bail!(
            "Interactive mode requires a terminal (TTY).\n\
             Run without -i flag, or use: repotoire findings --json"
        );
    }

    // Setup terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(findings, repo_path);
    let res = run_app(&mut terminal, &mut app);

    // Restore terminal
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;
    terminal.show_cursor()?;

    if let Err(err) = res {
        eprintln!("Error: {err:?}");
    }

    Ok(())
}

fn run_app(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>, app: &mut App) -> Result<()> {
    loop {
        terminal.draw(|f| ui(f, app))?;

        if let Event::Key(key) = event::read()? {
            if key.kind == KeyEventKind::Press {
                match key.code {
                    KeyCode::Char('q') | KeyCode::Esc if !app.show_detail => return Ok(()),
                    KeyCode::Esc => app.show_detail = false,
                    KeyCode::Down | KeyCode::Char('j') => {
                        if !app.show_detail {
                            app.next();
                        }
                    }
                    KeyCode::Up | KeyCode::Char('k') => {
                        if !app.show_detail {
                            app.previous();
                        }
                    }
                    KeyCode::Enter => app.show_detail = !app.show_detail,
                    KeyCode::PageDown => {
                        for _ in 0..10 {
                            app.next();
                        }
                    }
                    KeyCode::PageUp => {
                        for _ in 0..10 {
                            app.previous();
                        }
                    }
                    KeyCode::Char('f') => {
                        // Show fix command hint
                        if let Some(msg) = app.run_fix() {
                            app.status_message = Some((msg, false));
                        }
                    }
                    KeyCode::Char('F') => {
                        // Launch agent to fix + PR
                        if let Some(msg) = app.launch_agent() {
                            let is_error = msg.starts_with("❌");
                            app.status_message = Some((msg, is_error));
                        }
                    }
                    _ => {}
                }
            }
        }
    }
}

fn ui(f: &mut Frame, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(0),
            Constraint::Length(1),
        ])
        .split(f.area());

    // Header with stats
    let selected = app.list_state.selected().unwrap_or(0) + 1;
    let header = Paragraph::new(format!(
        " Repotoire | {} findings | {}/{}",
        app.findings.len(),
        selected,
        app.findings.len()
    ))
    .style(Style::default().fg(Color::Cyan).bold())
    .block(Block::default().borders(Borders::ALL));
    f.render_widget(header, chunks[0]);

    // Split main area into list and detail
    let main_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(40),
            Constraint::Percentage(60),
        ])
        .split(chunks[1]);

    // List (left pane)
    render_list(f, main_chunks[0], app);

    // Detail (right pane) - always visible
    if let Some(finding) = app.selected_finding().cloned() {
        let code = app.get_code_snippet().cloned();
        render_detail(f, main_chunks[1], &finding, code.as_ref());
    }

    // Footer - show status message if present, otherwise help
    let footer_text = if let Some((msg, is_error)) = &app.status_message {
        Line::from(vec![
            Span::styled(
                format!(" {} ", msg),
                if *is_error {
                    Style::default().fg(Color::Red)
                } else {
                    Style::default().fg(Color::Green)
                }
            ),
        ])
    } else {
        Line::from(vec![
            Span::styled(" j/k", Style::default().fg(Color::Cyan)),
            Span::raw(":Nav  "),
            Span::styled("Enter", Style::default().fg(Color::Cyan)),
            Span::raw(":Details  "),
            Span::styled("f", Style::default().fg(Color::Yellow)),
            Span::raw(":Fix  "),
            Span::styled("F", Style::default().fg(Color::Green).bold()),
            Span::raw(":Agent+PR  "),
            Span::styled("q", Style::default().fg(Color::Cyan)),
            Span::raw(":Quit"),
        ])
    };
    let footer = Paragraph::new(footer_text);
    f.render_widget(footer, chunks[2]);
}

fn render_list(f: &mut Frame, area: Rect, app: &mut App) {
    let items: Vec<ListItem> = app
        .findings
        .iter()
        .enumerate()
        .map(|(i, finding)| {
            let severity_char = match finding.severity {
                Severity::Critical => ("C", Color::Red),
                Severity::High => ("H", Color::Yellow),
                Severity::Medium => ("M", Color::Blue),
                Severity::Low => ("L", Color::DarkGray),
                Severity::Info => ("I", Color::DarkGray),
            };

            let file = finding
                .affected_files
                .first()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "unknown".to_string());
            
            // Truncate file path if too long
            let max_file_len = 40;
            let file_display = if file.len() > max_file_len {
                format!("...{}", &file[file.len() - max_file_len + 3..])
            } else {
                file
            };

            let line = Line::from(vec![
                Span::styled(
                    format!("{:>4} ", i + 1),
                    Style::default().fg(Color::DarkGray),
                ),
                Span::styled(
                    format!("[{}] ", severity_char.0),
                    Style::default().fg(severity_char.1).bold(),
                ),
                Span::raw(&finding.title),
                Span::styled(
                    format!("  {}", file_display),
                    Style::default().fg(Color::DarkGray),
                ),
            ]);

            ListItem::new(line)
        })
        .collect();

    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title(" Findings "))
        .highlight_style(Style::default().bg(Color::DarkGray).fg(Color::White))
        .highlight_symbol("> ");

    f.render_stateful_widget(list, area, &mut app.list_state);
}

fn render_detail(f: &mut Frame, area: Rect, finding: &Finding, code_snippet: Option<&Vec<(u32, String)>>) {
    let severity_str = match finding.severity {
        Severity::Critical => "CRITICAL",
        Severity::High => "HIGH",
        Severity::Medium => "MEDIUM",
        Severity::Low => "LOW",
        Severity::Info => "INFO",
    };

    let file = finding
        .affected_files
        .first()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "unknown".to_string());

    let line_start = finding.line_start.unwrap_or(0);
    let line_end = finding.line_end.unwrap_or(line_start);

    let line_info = match (finding.line_start, finding.line_end) {
        (Some(start), Some(end)) if start != end => format!(":{}-{}", start, end),
        (Some(start), _) => format!(":{}", start),
        _ => String::new(),
    };

    let mut text = vec![
        Line::from(vec![
            Span::styled("Title: ", Style::default().bold()),
            Span::raw(&finding.title),
        ]),
        Line::from(vec![
            Span::styled("Severity: ", Style::default().bold()),
            Span::styled(
                severity_str,
                Style::default().fg(match finding.severity {
                    Severity::Critical => Color::Red,
                    Severity::High => Color::Yellow,
                    Severity::Medium => Color::Blue,
                    _ => Color::DarkGray,
                }),
            ),
        ]),
        Line::from(vec![
            Span::styled("File: ", Style::default().bold()),
            Span::raw(format!("{}{}", file, line_info)),
        ]),
        Line::from(""),
    ];

    // Add code snippet
    if let Some(lines) = code_snippet {
        text.push(Line::from(Span::styled("Code:", Style::default().bold())));
        text.push(Line::from(""));
        
        for (line_num, code) in lines {
            let is_highlighted = *line_num >= line_start && *line_num <= line_end;
            let line_style = if is_highlighted {
                Style::default().bg(Color::DarkGray).fg(Color::White)
            } else {
                Style::default().fg(Color::DarkGray)
            };
            
            // Truncate long lines
            let display_code = if code.len() > 80 {
                format!("{}...", &code[..77])
            } else {
                code.clone()
            };
            
            text.push(Line::from(vec![
                Span::styled(format!("{:>4} | ", line_num), Style::default().fg(Color::DarkGray)),
                Span::styled(display_code, line_style),
            ]));
        }
        text.push(Line::from(""));
    }

    // Description
    text.push(Line::from(Span::styled("Description:", Style::default().bold())));
    for line in finding.description.lines().take(3) {
        text.push(Line::from(format!("  {}", line)));
    }

    if let Some(fix) = &finding.suggested_fix {
        text.push(Line::from(""));
        text.push(Line::from(Span::styled("Fix:", Style::default().bold())));
        for line in fix.lines().take(2) {
            text.push(Line::from(format!("  {}", line)));
        }
    }

    let paragraph = Paragraph::new(text)
        .block(Block::default().borders(Borders::ALL).title(" Details "))
        .wrap(Wrap { trim: false });

    f.render_widget(paragraph, area);
}
