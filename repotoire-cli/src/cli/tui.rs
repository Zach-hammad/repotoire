//! Interactive TUI for browsing findings
//!
//! Keys:
//! - j/k or Up/Down: Navigate findings
//! - Enter: Toggle detail view
//! - f: Fix current finding with AI (shows command)
//! - F: Launch agent to fix + create PR (async)
//! - a: Show running agents
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

use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use std::time::{Duration, Instant};

use crate::models::{Finding, Severity};

/// Status of a running agent
#[derive(Clone, Debug)]
pub enum AgentStatus {
    Running,
    Completed(bool), // success
    Failed(String),  // error message
}

/// Represents a running agent task
pub struct AgentTask {
    pub finding_index: usize,
    pub finding_title: String,
    pub started_at: Instant,
    pub status: AgentStatus,
    pub log_file: PathBuf,
    child: Option<Child>,
}

impl AgentTask {
    /// Check if the agent process has completed
    fn poll(&mut self) -> bool {
        if let Some(ref mut child) = self.child {
            match child.try_wait() {
                Ok(Some(status)) => {
                    if status.success() {
                        self.status = AgentStatus::Completed(true);
                    } else {
                        self.status = AgentStatus::Failed(format!("Exit code: {:?}", status.code()));
                    }
                    self.child = None;
                    true
                }
                Ok(None) => false, // Still running
                Err(e) => {
                    self.status = AgentStatus::Failed(format!("Poll error: {}", e));
                    self.child = None;
                    true
                }
            }
        } else {
            true // Already completed
        }
    }

    fn elapsed(&self) -> Duration {
        self.started_at.elapsed()
    }

    fn elapsed_str(&self) -> String {
        let secs = self.elapsed().as_secs();
        if secs < 60 {
            format!("{}s", secs)
        } else {
            format!("{}m{}s", secs / 60, secs % 60)
        }
    }
}

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

/// Get the agent log directory
fn get_agent_log_dir(repo_path: &Path) -> PathBuf {
    let dir = repo_path.join(".repotoire").join("agents");
    fs::create_dir_all(&dir).ok();
    dir
}

/// Read last N lines from a file
fn tail_file(path: &Path, n: usize) -> Vec<String> {
    if let Ok(file) = File::open(path) {
        let reader = BufReader::new(file);
        let lines: Vec<String> = reader.lines().filter_map(|l| l.ok()).collect();
        lines.into_iter().rev().take(n).rev().collect()
    } else {
        vec![]
    }
}

pub struct App {
    findings: Vec<Finding>,
    list_state: ListState,
    show_detail: bool,
    show_agents: bool,
    repo_path: PathBuf,
    code_cache: Option<Vec<(u32, String)>>,
    cached_index: Option<usize>,
    status_message: Option<(String, bool, Instant)>, // (message, is_error, when)
    agents: Vec<AgentTask>,
}

impl App {
    pub fn new(findings: Vec<Finding>, repo_path: PathBuf) -> Self {
        let mut list_state = ListState::default();
        if !findings.is_empty() {
            list_state.select(Some(0));
        }
        Self {
            findings,
            list_state,
            show_detail: false,
            show_agents: false,
            repo_path,
            code_cache: None,
            cached_index: None,
            status_message: None,
            agents: Vec::new(),
        }
    }

    /// Set a status message that auto-clears after 5 seconds
    fn set_status(&mut self, msg: String, is_error: bool) {
        self.status_message = Some((msg, is_error, Instant::now()));
    }

    /// Clear status if older than 5 seconds
    fn maybe_clear_status(&mut self) {
        if let Some((_, _, when)) = &self.status_message {
            if when.elapsed() > Duration::from_secs(5) {
                self.status_message = None;
            }
        }
    }

    /// Poll all running agents for status updates
    fn poll_agents(&mut self) {
        for agent in &mut self.agents {
            agent.poll();
        }
    }

    /// Get count of running agents
    fn running_agent_count(&self) -> usize {
        self.agents.iter().filter(|a| matches!(a.status, AgentStatus::Running)).count()
    }
    
    /// Launch Claude Code agent to fix the current finding and create a PR
    fn launch_agent(&mut self) -> Option<String> {
        let finding = self.selected_finding()?.clone();
        let index = self.list_state.selected()? + 1;
        
        // Check if agent already running for this finding
        if self.agents.iter().any(|a| a.finding_index == index && matches!(a.status, AgentStatus::Running)) {
            return Some(format!("⚠️ Agent already running for finding #{}", index));
        }
        
        let file = finding.affected_files.first()
            .map(|p| p.display().to_string())
            .unwrap_or_default();
        
        let line_start = finding.line_start.unwrap_or(1);
        let line_end = finding.line_end.unwrap_or(line_start);
        
        // Read actual code snippet for context
        let code_context = read_code_snippet(&self.repo_path, &file, line_start, line_end, 5)
            .map(|lines| {
                lines.iter()
                    .map(|(n, l)| format!("{:4} | {}", n, l))
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_else(|| "Code not available".to_string());
        
        // Create a detailed task description
        let task = format!(
            r#"Fix this code issue:

## Finding
- **Title:** {}
- **Severity:** {:?}
- **File:** {}
- **Lines:** {}-{}

## Description
{}

## Code Context
```
{}
```

## Suggested Fix
{}

## Your Task
1. Read the file: {}
2. Fix the issue at lines {}-{}
3. Run: git checkout -b fix/finding-{}
4. Commit your fix with message: "fix: {}"
5. Run: git push -u origin fix/finding-{}
6. Run: gh pr create --title "fix: {}" --body "Fixes finding #{} - {}"

Be precise. Make minimal changes. Test if possible."#,
            finding.title,
            finding.severity,
            file,
            line_start, line_end,
            finding.description,
            code_context,
            finding.suggested_fix.as_deref().unwrap_or("Apply appropriate fix based on the description."),
            file,
            line_start, line_end,
            index,
            finding.title,
            index,
            finding.title,
            index,
            finding.title
        );
        
        // Create log file for this agent
        let log_dir = get_agent_log_dir(&self.repo_path);
        let log_file = log_dir.join(format!("agent_{}.log", index));
        
        // Open log file for writing
        let log_handle = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&log_file);
        
        let stdout_file = match log_handle {
            Ok(f) => f,
            Err(e) => return Some(format!("❌ Failed to create log file: {}", e)),
        };
        
        let stderr_file = stdout_file.try_clone().ok();
        
        // Spawn Claude Code with proper args
        let result = Command::new("claude")
            .args([
                "--print",
                "--dangerously-skip-permissions",
                "--permission-mode", "bypassPermissions",
                &task
            ])
            .current_dir(&self.repo_path)
            .stdout(Stdio::from(stdout_file))
            .stderr(stderr_file.map(Stdio::from).unwrap_or(Stdio::null()))
            .spawn();
        
        match result {
            Ok(child) => {
                let pid = child.id();
                self.agents.push(AgentTask {
                    finding_index: index,
                    finding_title: finding.title.clone(),
                    started_at: Instant::now(),
                    status: AgentStatus::Running,
                    log_file: log_file.clone(),
                    child: Some(child),
                });
                Some(format!("🚀 Agent launched (PID: {}) - log: {}", pid, log_file.display()))
            }
            Err(e) => {
                Some(format!("❌ Failed to launch agent: {}. Install: npm i -g @anthropic-ai/claude-code", e))
            }
        }
    }
    
    /// Run the built-in fix command for the current finding
    fn run_fix(&self) -> Option<String> {
        let index = self.list_state.selected()? + 1;
        Some(format!("Run: repotoire fix {}", index))
    }

    fn get_code_snippet(&mut self) -> Option<&Vec<(u32, String)>> {
        let selected = self.list_state.selected()?;
        
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
            3,
        );
        self.cached_index = Some(selected);
        self.code_cache.as_ref()
    }

    fn next(&mut self) {
        if self.findings.is_empty() { return; }
        let i = match self.list_state.selected() {
            Some(i) => (i + 1) % self.findings.len(),
            None => 0,
        };
        self.list_state.select(Some(i));
    }

    fn previous(&mut self) {
        if self.findings.is_empty() { return; }
        let i = match self.list_state.selected() {
            Some(i) => if i == 0 { self.findings.len() - 1 } else { i - 1 },
            None => 0,
        };
        self.list_state.select(Some(i));
    }

    fn selected_finding(&self) -> Option<&Finding> {
        self.list_state.selected().and_then(|i| self.findings.get(i))
    }
}

pub fn run(findings: Vec<Finding>, repo_path: PathBuf) -> Result<()> {
    use std::io::IsTerminal;
    if !io::stdout().is_terminal() {
        anyhow::bail!(
            "Interactive mode requires a terminal (TTY).\n\
             Run without -i flag, or use: repotoire findings --json"
        );
    }

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(findings, repo_path);
    let res = run_app(&mut terminal, &mut app);

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
        // Poll agents for status updates
        app.poll_agents();
        app.maybe_clear_status();
        
        terminal.draw(|f| ui(f, app))?;

        // Non-blocking event check with timeout for agent polling
        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    match key.code {
                        KeyCode::Char('q') | KeyCode::Esc if !app.show_detail && !app.show_agents => return Ok(()),
                        KeyCode::Esc => {
                            app.show_detail = false;
                            app.show_agents = false;
                        }
                        KeyCode::Down | KeyCode::Char('j') if !app.show_agents => app.next(),
                        KeyCode::Up | KeyCode::Char('k') if !app.show_agents => app.previous(),
                        KeyCode::Enter if !app.show_agents => app.show_detail = !app.show_detail,
                        KeyCode::PageDown => { for _ in 0..10 { app.next(); } }
                        KeyCode::PageUp => { for _ in 0..10 { app.previous(); } }
                        KeyCode::Char('f') => {
                            if let Some(msg) = app.run_fix() {
                                app.set_status(msg, false);
                            }
                        }
                        KeyCode::Char('F') => {
                            if let Some(msg) = app.launch_agent() {
                                let is_error = msg.starts_with("❌") || msg.starts_with("⚠️");
                                app.set_status(msg, is_error);
                            }
                        }
                        KeyCode::Char('a') | KeyCode::Char('A') => {
                            app.show_agents = !app.show_agents;
                        }
                        _ => {}
                    }
                }
            }
        }
    }
}

fn ui(f: &mut Frame, app: &mut App) {
    let running = app.running_agent_count();
    
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(0),
            Constraint::Length(if running > 0 { 2 } else { 1 }),
        ])
        .split(f.area());

    // Header
    let selected = app.list_state.selected().unwrap_or(0) + 1;
    let agent_indicator = if running > 0 {
        format!(" | 🤖 {} agent{}", running, if running > 1 { "s" } else { "" })
    } else {
        String::new()
    };
    let header = Paragraph::new(format!(
        " 🎼 Repotoire | {} findings | {}/{}{}",
        app.findings.len(),
        selected,
        app.findings.len(),
        agent_indicator
    ))
    .style(Style::default().fg(Color::Cyan).bold())
    .block(Block::default().borders(Borders::ALL));
    f.render_widget(header, chunks[0]);

    // Main content
    if app.show_agents {
        render_agents_panel(f, chunks[1], app);
    } else {
        let main_chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(40), Constraint::Percentage(60)])
            .split(chunks[1]);

        render_list(f, main_chunks[0], app);
        
        if let Some(finding) = app.selected_finding().cloned() {
            let code = app.get_code_snippet().cloned();
            render_detail(f, main_chunks[1], &finding, code.as_ref());
        }
    }

    // Footer
    render_footer(f, chunks[2], app);
}

fn render_agents_panel(f: &mut Frame, area: Rect, app: &App) {
    let items: Vec<ListItem> = app.agents.iter().map(|agent| {
        let (status_icon, status_color) = match &agent.status {
            AgentStatus::Running => ("⏳", Color::Yellow),
            AgentStatus::Completed(true) => ("✅", Color::Green),
            AgentStatus::Completed(false) => ("❌", Color::Red),
            AgentStatus::Failed(_) => ("💥", Color::Red),
        };
        
        let line = Line::from(vec![
            Span::styled(format!(" {} ", status_icon), Style::default().fg(status_color)),
            Span::styled(format!("#{:<3} ", agent.finding_index), Style::default().fg(Color::Cyan)),
            Span::raw(&agent.finding_title),
            Span::styled(format!("  [{}]", agent.elapsed_str()), Style::default().fg(Color::DarkGray)),
        ]);
        
        ListItem::new(line)
    }).collect();

    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title(" Running Agents (press 'a' to close) "));
    f.render_widget(list, area);
}

fn render_list(f: &mut Frame, area: Rect, app: &mut App) {
    let items: Vec<ListItem> = app.findings.iter().enumerate().map(|(i, finding)| {
        let (severity_char, severity_color) = match finding.severity {
            Severity::Critical => ("C", Color::Red),
            Severity::High => ("H", Color::Yellow),
            Severity::Medium => ("M", Color::Blue),
            Severity::Low => ("L", Color::DarkGray),
            Severity::Info => ("I", Color::DarkGray),
        };

        let file = finding.affected_files.first()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "unknown".to_string());
        
        let max_len = 40;
        let file_display = if file.len() > max_len {
            format!("...{}", &file[file.len() - max_len + 3..])
        } else {
            file
        };

        // Check if agent is running for this finding
        let agent_icon = if app.agents.iter().any(|a| a.finding_index == i + 1 && matches!(a.status, AgentStatus::Running)) {
            "🤖 "
        } else {
            ""
        };

        let line = Line::from(vec![
            Span::styled(format!("{:>4} ", i + 1), Style::default().fg(Color::DarkGray)),
            Span::styled(format!("[{}] ", severity_char), Style::default().fg(severity_color).bold()),
            Span::raw(agent_icon),
            Span::raw(&finding.title),
            Span::styled(format!("  {}", file_display), Style::default().fg(Color::DarkGray)),
        ]);

        ListItem::new(line)
    }).collect();

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

    let file = finding.affected_files.first()
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
            Span::styled(severity_str, Style::default().fg(match finding.severity {
                Severity::Critical => Color::Red,
                Severity::High => Color::Yellow,
                Severity::Medium => Color::Blue,
                _ => Color::DarkGray,
            })),
        ]),
        Line::from(vec![
            Span::styled("File: ", Style::default().bold()),
            Span::raw(format!("{}{}", file, line_info)),
        ]),
        Line::from(""),
    ];

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

fn render_footer(f: &mut Frame, area: Rect, app: &App) {
    let running = app.running_agent_count();
    
    // If there's a status message, show it
    if let Some((msg, is_error, _)) = &app.status_message {
        let footer = Paragraph::new(Line::from(vec![
            Span::styled(
                format!(" {} ", msg),
                if *is_error { Style::default().fg(Color::Red) } else { Style::default().fg(Color::Green) }
            ),
        ]));
        f.render_widget(footer, area);
        return;
    }
    
    // Show agents status if any running
    if running > 0 {
        let footer_chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Length(1)])
            .split(area);
        
        let keybinds = Line::from(vec![
            Span::styled(" j/k", Style::default().fg(Color::Cyan)),
            Span::raw(":Nav  "),
            Span::styled("Enter", Style::default().fg(Color::Cyan)),
            Span::raw(":Details  "),
            Span::styled("f", Style::default().fg(Color::Yellow)),
            Span::raw(":Fix  "),
            Span::styled("F", Style::default().fg(Color::Green).bold()),
            Span::raw(":Agent  "),
            Span::styled("a", Style::default().fg(Color::Magenta)),
            Span::raw(":Agents  "),
            Span::styled("q", Style::default().fg(Color::Cyan)),
            Span::raw(":Quit"),
        ]);
        f.render_widget(Paragraph::new(keybinds), footer_chunks[0]);
        
        let agent_status = Line::from(vec![
            Span::styled(
                format!(" 🤖 {} agent{} running... ", running, if running > 1 { "s" } else { "" }),
                Style::default().fg(Color::Yellow)
            ),
        ]);
        f.render_widget(Paragraph::new(agent_status), footer_chunks[1]);
    } else {
        let footer = Paragraph::new(Line::from(vec![
            Span::styled(" j/k", Style::default().fg(Color::Cyan)),
            Span::raw(":Nav  "),
            Span::styled("Enter", Style::default().fg(Color::Cyan)),
            Span::raw(":Details  "),
            Span::styled("f", Style::default().fg(Color::Yellow)),
            Span::raw(":Fix  "),
            Span::styled("F", Style::default().fg(Color::Green).bold()),
            Span::raw(":Agent+PR  "),
            Span::styled("a", Style::default().fg(Color::Magenta)),
            Span::raw(":Agents  "),
            Span::styled("q", Style::default().fg(Color::Cyan)),
            Span::raw(":Quit"),
        ]));
        f.render_widget(footer, area);
    }
}
