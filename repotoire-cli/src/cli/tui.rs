//! Interactive TUI for browsing findings

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
use std::io;

use crate::models::{Finding, Severity};

pub struct App {
    findings: Vec<Finding>,
    list_state: ListState,
    show_detail: bool,
}

impl App {
    pub fn new(findings: Vec<Finding>) -> Self {
        let mut list_state = ListState::default();
        if !findings.is_empty() {
            list_state.select(Some(0));
        }
        Self {
            findings,
            list_state,
            show_detail: false,
        }
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

pub fn run(findings: Vec<Finding>) -> Result<()> {
    // Setup terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(findings);
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
            Constraint::Length(3),
        ])
        .split(f.area());

    // Header
    let header = Paragraph::new(format!(
        " Repotoire - {} findings",
        app.findings.len()
    ))
    .style(Style::default().fg(Color::Cyan).bold())
    .block(Block::default().borders(Borders::ALL));
    f.render_widget(header, chunks[0]);

    if app.show_detail {
        // Detail view
        if let Some(finding) = app.selected_finding() {
            render_detail(f, chunks[1], finding);
        }
    } else {
        // List view
        render_list(f, chunks[1], app);
    }

    // Footer
    let help = if app.show_detail {
        " [Enter/Esc] Back  [q] Quit"
    } else {
        " [j/k] Navigate  [Enter] Details  [q] Quit"
    };
    let footer = Paragraph::new(help)
        .style(Style::default().fg(Color::DarkGray))
        .block(Block::default().borders(Borders::ALL));
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

fn render_detail(f: &mut Frame, area: Rect, finding: &Finding) {
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
            Span::styled("Detector: ", Style::default().bold()),
            Span::raw(&finding.detector),
        ]),
        Line::from(vec![
            Span::styled("File: ", Style::default().bold()),
            Span::raw(format!("{}{}", file, line_info)),
        ]),
        Line::from(""),
        Line::from(Span::styled("Description:", Style::default().bold())),
    ];

    for line in finding.description.lines() {
        text.push(Line::from(format!("  {}", line)));
    }

    if let Some(fix) = &finding.suggested_fix {
        text.push(Line::from(""));
        text.push(Line::from(Span::styled("Suggested Fix:", Style::default().bold())));
        for line in fix.lines() {
            text.push(Line::from(format!("  {}", line)));
        }
    }

    if let Some(why) = &finding.why_it_matters {
        text.push(Line::from(""));
        text.push(Line::from(Span::styled("Why It Matters:", Style::default().bold())));
        for line in why.lines() {
            text.push(Line::from(format!("  {}", line)));
        }
    }

    let paragraph = Paragraph::new(text)
        .block(Block::default().borders(Borders::ALL).title(format!(" Finding Details ")))
        .wrap(Wrap { trim: false });

    f.render_widget(paragraph, area);
}
