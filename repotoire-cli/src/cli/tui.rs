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

use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use std::time::{Duration, Instant};

use crate::cli::embedded_scripts;
use crate::config::UserConfig;
use crate::models::{Finding, Severity};
use crate::tui::{
    hide_cursor, install_panic_hook, poll_key, read_key, show_cursor, split_horizontal,
    split_vertical, AltScreenGuard, Color, Constraint, Key, RawModeGuard, Rect, Screen, Style,
};

/// Status of a running agent
#[derive(Clone, Debug)]
pub enum AgentStatus {
    Running,
    Completed(bool),                    // success
    Failed(#[allow(dead_code)] String), // error message
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
    fn poll(&mut self) -> bool {
        let Some(ref mut child) = self.child else {
            return true;
        };
        match child.try_wait() {
            Ok(Some(status)) => {
                self.status = agent_status_from_exit(status);
                self.child = None;
                true
            }
            Ok(None) => false,
            Err(e) => {
                self.status = AgentStatus::Failed(format!("Poll error: {}", e));
                self.child = None;
                true
            }
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

    fn cancel(&mut self) -> bool {
        let Some(ref mut child) = self.child else {
            return false;
        };
        if child.kill().is_err() {
            return false;
        }
        self.status = AgentStatus::Failed("Cancelled by user".to_string());
        self.child = None;
        true
    }
}

fn agent_status_from_exit(status: std::process::ExitStatus) -> AgentStatus {
    if status.success() {
        AgentStatus::Completed(true)
    } else {
        AgentStatus::Failed(format!("Exit code: {:?}", status.code()))
    }
}

fn format_spawn_error(error: std::io::Error, use_ollama: bool) -> String {
    if use_ollama {
        format!("Failed: {}. Is Ollama running? (ollama serve)", error)
    } else {
        format!("Failed: {}. Install claude-code or set up venv.", error)
    }
}

fn resolve_api_key(config: &UserConfig) -> Option<String> {
    if let Some(key) = config.anthropic_api_key() {
        return Some(key.to_string());
    }
    std::env::var("ANTHROPIC_API_KEY").ok()
}

fn read_code_snippet(
    repo_path: &Path,
    file_path: &str,
    line_start: u32,
    line_end: u32,
    context: usize,
) -> Option<Vec<(u32, String)>> {
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
            .collect(),
    )
}

fn get_agent_log_dir(repo_path: &Path) -> PathBuf {
    let dir = repo_path.join(".repotoire").join("agents");
    fs::create_dir_all(&dir).ok();
    dir
}

fn tail_file(path: &Path, n: usize) -> Vec<String> {
    if let Ok(file) = File::open(path) {
        let reader = BufReader::new(file);
        let lines: Vec<String> = reader.lines().map_while(Result::ok).collect();
        lines.into_iter().rev().take(n).rev().collect()
    } else {
        vec![]
    }
}

const SPINNER_FRAMES: [char; 4] = ['|', '/', '-', '\\'];

pub struct App {
    findings: Vec<Finding>,
    selected: usize,
    scroll_offset: usize,
    show_detail: bool,
    show_agents: bool,
    repo_path: PathBuf,
    code_cache: Option<Vec<(u32, String)>>,
    cached_index: Option<usize>,
    status_message: Option<(String, bool, Instant)>,
    agents: Vec<AgentTask>,
    config: UserConfig,
    frame: usize,
}

impl App {
    pub fn new(findings: Vec<Finding>, repo_path: PathBuf) -> Self {
        let config = UserConfig::load().unwrap_or_default();
        Self {
            findings,
            selected: 0,
            scroll_offset: 0,
            show_detail: false,
            show_agents: false,
            repo_path,
            code_cache: None,
            cached_index: None,
            status_message: None,
            agents: Vec::new(),
            config,
            frame: 0,
        }
    }

    fn spinner(&self) -> char {
        SPINNER_FRAMES[self.frame % SPINNER_FRAMES.len()]
    }

    fn set_status(&mut self, msg: String, is_error: bool) {
        self.status_message = Some((msg, is_error, Instant::now()));
    }

    fn maybe_clear_status(&mut self) {
        if let Some((_, _, when)) = &self.status_message {
            if when.elapsed() > Duration::from_secs(5) {
                self.status_message = None;
            }
        }
    }

    fn poll_agents(&mut self) {
        for agent in &mut self.agents {
            agent.poll();
        }
    }

    fn running_agent_count(&self) -> usize {
        self.agents
            .iter()
            .filter(|a| matches!(a.status, AgentStatus::Running))
            .count()
    }

    fn cancel_latest_agent(&mut self) -> Option<String> {
        if let Some(agent) = self
            .agents
            .iter_mut()
            .rev()
            .find(|a| matches!(a.status, AgentStatus::Running))
        {
            let title = agent.finding_title.clone();
            let index = agent.finding_index;
            if agent.cancel() {
                Some(format!("Cancelled agent #{}: {}", index, title))
            } else {
                Some(format!("Failed to cancel agent #{}", index))
            }
        } else {
            Some("No running agents to cancel".to_string())
        }
    }

    fn launch_agent(&mut self) -> Option<String> {
        let finding = self.selected_finding()?.clone();
        let index = self.selected + 1;

        if self
            .agents
            .iter()
            .any(|a| a.finding_index == index && matches!(a.status, AgentStatus::Running))
        {
            return Some(format!("Agent already running for finding #{}", index));
        }

        let use_ollama = self.config.use_ollama();

        let api_key = if use_ollama {
            String::new()
        } else {
            match resolve_api_key(&self.config) {
                Some(key) => key,
                None => return Some("No API key. Run: repotoire config init".to_string()),
            }
        };

        let log_dir = get_agent_log_dir(&self.repo_path);
        let log_file = log_dir.join(format!("agent_{}.log", index));

        let log_handle = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&log_file);

        let stdout_file = match log_handle {
            Ok(f) => f,
            Err(e) => return Some(format!("Failed to create log file: {}", e)),
        };

        let stderr_file = stdout_file.try_clone().ok();

        let finding_json = serde_json::json!({
            "index": index,
            "title": finding.title,
            "severity": finding.severity.to_string(),
            "description": finding.description,
            "suggested_fix": finding.suggested_fix,
            "affected_files": finding.affected_files.iter().map(|p| p.display().to_string()).collect::<Vec<_>>(),
            "line_start": finding.line_start,
            "line_end": finding.line_end,
        });

        let (ollama_script, claude_script) =
            match embedded_scripts::get_script_paths(&self.repo_path) {
                Ok(paths) => paths,
                Err(e) => return Some(format!("Failed to extract scripts: {}", e)),
            };

        let venv_python = self.repo_path.join(".venv/bin/python");
        let system_python = PathBuf::from("python3");
        let python = if venv_python.exists() {
            &venv_python
        } else {
            &system_python
        };

        let ollama_script_str = ollama_script.to_string_lossy().to_string();
        let claude_script_str = claude_script.to_string_lossy().to_string();
        let repo_path_str = self.repo_path.to_string_lossy().to_string();

        let result = if use_ollama {
            Command::new(python)
                .args([
                    ollama_script_str.as_str(),
                    "--finding-json",
                    &finding_json.to_string(),
                    "--repo-path",
                    repo_path_str.as_str(),
                    "--model",
                    self.config.ollama_model(),
                ])
                .env("OLLAMA_URL", self.config.ollama_url())
                .current_dir(&self.repo_path)
                .stdout(Stdio::from(stdout_file))
                .stderr(stderr_file.map(Stdio::from).unwrap_or(Stdio::null()))
                .spawn()
        } else {
            Command::new(python)
                .args([
                    claude_script_str.as_str(),
                    "--finding-json",
                    &finding_json.to_string(),
                    "--repo-path",
                    repo_path_str.as_str(),
                ])
                .env("ANTHROPIC_API_KEY", &api_key)
                .current_dir(&self.repo_path)
                .stdout(Stdio::from(stdout_file))
                .stderr(stderr_file.map(Stdio::from).unwrap_or(Stdio::null()))
                .spawn()
        };

        let backend_name = if use_ollama {
            format!("Ollama ({})", self.config.ollama_model())
        } else {
            "Claude".to_string()
        };

        let child = match result {
            Ok(c) => c,
            Err(e) => return Some(format_spawn_error(e, use_ollama)),
        };
        let pid = child.id();
        self.agents.push(AgentTask {
            finding_index: index,
            finding_title: finding.title.clone(),
            started_at: Instant::now(),
            status: AgentStatus::Running,
            log_file: log_file.clone(),
            child: Some(child),
        });
        Some(format!("{} agent launched (PID: {})", backend_name, pid))
    }

    fn run_fix(&self) -> Option<String> {
        let index = self.selected + 1;
        Some(format!("Run: repotoire fix {}", index))
    }

    fn get_code_snippet(&mut self) -> Option<&Vec<(u32, String)>> {
        if self.cached_index == Some(self.selected) {
            return self.code_cache.as_ref();
        }
        let finding = self.findings.get(self.selected)?;
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
        self.cached_index = Some(self.selected);
        self.code_cache.as_ref()
    }

    fn next(&mut self) {
        if !self.findings.is_empty() {
            self.selected = (self.selected + 1) % self.findings.len();
        }
    }

    fn previous(&mut self) {
        if !self.findings.is_empty() {
            if self.selected == 0 {
                self.selected = self.findings.len() - 1;
            } else {
                self.selected -= 1;
            }
        }
    }

    fn selected_finding(&self) -> Option<&Finding> {
        self.findings.get(self.selected)
    }

    /// Ensure the selected item is visible in the list viewport.
    fn adjust_scroll(&mut self, visible_height: u16) {
        let vh = visible_height as usize;
        if vh == 0 {
            return;
        }
        if self.selected < self.scroll_offset {
            self.scroll_offset = self.selected;
        } else if self.selected >= self.scroll_offset + vh {
            self.scroll_offset = self.selected - vh + 1;
        }
    }
}

// ============================================================================
// ENTRY POINT
// ============================================================================

pub fn run(findings: Vec<Finding>, repo_path: PathBuf) -> Result<()> {
    use std::io::IsTerminal;
    if !io::stdout().is_terminal() {
        anyhow::bail!(
            "Interactive mode requires a terminal (TTY).\n\
             Run without -i flag, or use: repotoire findings --json"
        );
    }

    install_panic_hook();
    let _raw = RawModeGuard::enter()?;
    let _alt = AltScreenGuard::enter()?;
    hide_cursor()?;

    let (w, h) = crate::tui::term::terminal_size()?;
    let mut screen = Screen::new(w, h);
    let mut app = App::new(findings, repo_path);

    loop {
        app.poll_agents();
        app.maybe_clear_status();
        app.frame = app.frame.wrapping_add(1);

        screen.begin_frame();
        ui(&mut screen, &mut app);
        screen.end_frame()?;

        if !poll_key(Duration::from_millis(100))? {
            continue;
        }
        let key = read_key()?;
        if handle_key_event(&mut app, key) {
            break;
        }
    }

    show_cursor()?;
    Ok(())
}

// ============================================================================
// KEY HANDLING
// ============================================================================

fn handle_key_event(app: &mut App, key: Key) -> bool {
    match key {
        Key::Char('q') | Key::Escape if !app.show_detail && !app.show_agents => return true,
        Key::Escape => {
            app.show_detail = false;
            app.show_agents = false;
        }
        _ => handle_key_action(app, key),
    }
    false
}

fn handle_key_action(app: &mut App, key: Key) {
    match key {
        Key::Down | Key::Char('j') if !app.show_agents => app.next(),
        Key::Up | Key::Char('k') if !app.show_agents => app.previous(),
        Key::Enter if !app.show_agents => app.show_detail = !app.show_detail,
        Key::PageDown => (0..10).for_each(|_| app.next()),
        Key::PageUp => (0..10).for_each(|_| app.previous()),
        Key::Char('f') => {
            if let Some(msg) = app.run_fix() {
                app.set_status(msg, false);
            }
        }
        Key::Char('F') => {
            if let Some(msg) = app.launch_agent() {
                let is_error = msg.starts_with("Failed") || msg.starts_with("No ");
                app.set_status(msg, is_error);
            }
        }
        Key::Char('a') | Key::Char('A') => app.show_agents = !app.show_agents,
        Key::Char('c') => {
            if let Some(msg) = app.cancel_latest_agent() {
                let is_error = msg.starts_with("Failed") || msg.starts_with("No ");
                app.set_status(msg, is_error);
            }
        }
        _ => {}
    }
}

// ============================================================================
// RENDERING
// ============================================================================

fn ui(screen: &mut Screen, app: &mut App) {
    let running = app.running_agent_count();
    let area = screen.area();

    let chunks = split_vertical(
        area,
        &[
            Constraint::Length(3),
            Constraint::Min(0),
            Constraint::Length(if running > 0 { 2 } else { 1 }),
        ],
    );

    // Header
    render_header(screen, chunks[0], app);

    // Main content
    if app.show_agents {
        render_agents_panel(screen, chunks[1], app);
    } else {
        let main_chunks =
            split_horizontal(chunks[1], &[Constraint::Percentage(40), Constraint::Percentage(60)]);
        render_list(screen, main_chunks[0], app);
        if let Some(finding) = app.selected_finding().cloned() {
            let code = app.get_code_snippet().cloned();
            render_detail(screen, main_chunks[1], &finding, code.as_ref());
        }
    }

    // Footer
    render_footer(screen, chunks[2], app);
}

fn render_header(screen: &mut Screen, area: Rect, app: &App) {
    let running = app.running_agent_count();
    let inner = screen.current.draw_border(area, "", Style::default());

    let agent_indicator = if running > 0 {
        format!(
            " | {} agent{}",
            running,
            if running > 1 { "s" } else { "" }
        )
    } else {
        String::new()
    };
    let header_text = format!(
        " Repotoire | {} findings | {}/{}{}",
        app.findings.len(),
        app.selected + 1,
        app.findings.len(),
        agent_indicator
    );
    screen.current.set_str(
        inner.x,
        inner.y,
        &header_text,
        Style::default().fg(Color::Cyan).bold(),
    );
}

fn render_list(screen: &mut Screen, area: Rect, app: &mut App) {
    let inner = screen.current.draw_border(area, " Findings ", Style::default());
    let visible = inner.height as usize;
    app.adjust_scroll(inner.height);

    for (vi, i) in (app.scroll_offset..app.findings.len())
        .take(visible)
        .enumerate()
    {
        let finding = &app.findings[i];
        let y = inner.y + vi as u16;
        let is_selected = i == app.selected;

        let (sev_char, sev_color) = match finding.severity {
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
            .unwrap_or_default();
        let max_len = 40;
        let file_display = if file.len() > max_len {
            format!("...{}", &file[file.len() - max_len + 3..])
        } else {
            file
        };

        let bg = if is_selected {
            Color::DarkGray
        } else {
            Color::Reset
        };

        // Highlight symbol
        let prefix = if is_selected { "> " } else { "  " };

        // Build line piece by piece
        let mut x = inner.x;
        let base = Style::default().bg(bg);

        // "> " or "  "
        screen.current.set_str(x, y, prefix, base.fg(Color::White));
        x += 2;

        // Index
        let idx_str = format!("{:>4} ", i + 1);
        screen
            .current
            .set_str(x, y, &idx_str, base.fg(Color::DarkGray));
        x += idx_str.len() as u16;

        // Severity
        let sev_str = format!("[{}] ", sev_char);
        screen
            .current
            .set_str(x, y, &sev_str, Style { fg: sev_color, bg, bold: true });
        x += sev_str.len() as u16;

        // Agent icon
        if app.agents.iter().any(|a| {
            a.finding_index == i + 1 && matches!(a.status, AgentStatus::Running)
        }) {
            screen.current.set_str(x, y, "* ", base.fg(Color::Cyan));
            x += 2;
        }

        // Title (truncate to fit)
        let title_max = (inner.width as usize).saturating_sub((x - inner.x) as usize + file_display.len() + 2);
        let title = if finding.title.len() > title_max {
            &finding.title[..title_max]
        } else {
            &finding.title
        };
        screen.current.set_str(x, y, title, base.fg(if is_selected { Color::White } else { Color::Reset }));
        x += title.len() as u16;

        // File path (right-aligned-ish)
        let file_str = format!("  {}", file_display);
        screen
            .current
            .set_str(x, y, &file_str, base.fg(Color::DarkGray));

        // Fill rest of line with bg color for highlight
        if is_selected {
            let line_end = x + file_str.len() as u16;
            for fill_x in line_end..inner.x + inner.width {
                screen.current.set(fill_x, y, ' ', base);
            }
        }
    }
}

fn render_detail(
    screen: &mut Screen,
    area: Rect,
    finding: &Finding,
    code_snippet: Option<&Vec<(u32, String)>>,
) {
    let inner = screen.current.draw_border(area, " Details ", Style::default());
    let mut y = inner.y;

    let bold = Style::default().bold();

    // Title
    screen.current.set_str(inner.x, y, "Title: ", bold);
    screen
        .current
        .set_str(inner.x + 7, y, &finding.title, Style::default());
    y += 1;

    // Severity
    let severity_str = match finding.severity {
        Severity::Critical => "CRITICAL",
        Severity::High => "HIGH",
        Severity::Medium => "MEDIUM",
        Severity::Low => "LOW",
        Severity::Info => "INFO",
    };
    let sev_color = match finding.severity {
        Severity::Critical => Color::Red,
        Severity::High => Color::Yellow,
        Severity::Medium => Color::Blue,
        _ => Color::DarkGray,
    };
    screen.current.set_str(inner.x, y, "Severity: ", bold);
    screen.current.set_str(
        inner.x + 10,
        y,
        severity_str,
        Style::default().fg(sev_color),
    );
    y += 1;

    // File
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
    screen.current.set_str(inner.x, y, "File: ", bold);
    screen.current.set_str(
        inner.x + 6,
        y,
        &format!("{}{}", file, line_info),
        Style::default(),
    );
    y += 2;

    // Code snippet
    if let Some(lines) = code_snippet {
        let line_start = finding.line_start.unwrap_or(0);
        let line_end = finding.line_end.unwrap_or(line_start);

        screen.current.set_str(inner.x, y, "Code:", bold);
        y += 1;

        for (line_num, code) in lines {
            if y >= inner.y + inner.height {
                break;
            }
            let is_highlighted = *line_num >= line_start && *line_num <= line_end;
            let line_style = if is_highlighted {
                Style::default().bg(Color::DarkGray).fg(Color::White)
            } else {
                Style::default().fg(Color::DarkGray)
            };

            let max_code_width = (inner.width as usize).saturating_sub(7);
            let display_code = if code.len() > max_code_width {
                format!("{}...", &code[..max_code_width.saturating_sub(3)])
            } else {
                code.clone()
            };

            let prefix = format!("{:>4} | ", line_num);
            screen.current.set_str(
                inner.x,
                y,
                &prefix,
                Style::default().fg(Color::DarkGray),
            );
            screen
                .current
                .set_str(inner.x + prefix.len() as u16, y, &display_code, line_style);
            y += 1;
        }
        y += 1;
    }

    // Description
    if y < inner.y + inner.height {
        screen.current.set_str(inner.x, y, "Description:", bold);
        y += 1;
        for line in finding.description.lines().take(3) {
            if y >= inner.y + inner.height {
                break;
            }
            let desc = format!("  {}", line);
            screen
                .current
                .set_str(inner.x, y, &desc, Style::default());
            y += 1;
        }
    }

    // Suggested fix
    if let Some(fix) = &finding.suggested_fix {
        if y + 1 < inner.y + inner.height {
            y += 1;
            screen.current.set_str(inner.x, y, "Fix:", bold);
            y += 1;
            for line in fix.lines().take(2) {
                if y >= inner.y + inner.height {
                    break;
                }
                let fix_line = format!("  {}", line);
                screen
                    .current
                    .set_str(inner.x, y, &fix_line, Style::default());
                y += 1;
            }
        }
    }
}

fn render_agents_panel(screen: &mut Screen, area: Rect, app: &App) {
    let agent_list_height = (app.agents.len() as u16 + 2).min(6);
    let chunks = split_vertical(
        area,
        &[Constraint::Length(agent_list_height), Constraint::Min(5)],
    );

    // Agent list
    let inner = screen.current.draw_border(
        chunks[0],
        " Agents (press 'a' to close) ",
        Style::default(),
    );
    for (i, agent) in app.agents.iter().enumerate() {
        if i as u16 >= inner.height {
            break;
        }
        let y = inner.y + i as u16;
        let (status_icon, status_color) = match &agent.status {
            AgentStatus::Running => ("~", Color::Yellow),
            AgentStatus::Completed(true) => ("+", Color::Green),
            AgentStatus::Completed(false) => ("x", Color::Red),
            AgentStatus::Failed(_) => ("!", Color::Red),
        };

        let mut x = inner.x;
        let entry = format!(" {} ", status_icon);
        screen
            .current
            .set_str(x, y, &entry, Style::default().fg(status_color));
        x += entry.len() as u16;

        let idx = format!("#{:<3} ", agent.finding_index);
        screen
            .current
            .set_str(x, y, &idx, Style::default().fg(Color::Cyan));
        x += idx.len() as u16;

        screen
            .current
            .set_str(x, y, &agent.finding_title, Style::default());
        x += agent.finding_title.len() as u16;

        let elapsed = format!("  [{}]", agent.elapsed_str());
        screen
            .current
            .set_str(x, y, &elapsed, Style::default().fg(Color::DarkGray));
    }

    // Log output
    let log_inner =
        screen
            .current
            .draw_border(chunks[1], " Agent Output ", Style::default());
    let log_lines: Vec<String> = app
        .agents
        .iter()
        .rfind(|a| matches!(a.status, AgentStatus::Running))
        .map(|agent| tail_file(&agent.log_file, log_inner.height as usize))
        .unwrap_or_else(|| {
            vec![" No running agents - press 'F' on a finding to launch one".to_string()]
        });

    for (i, line) in log_lines.iter().enumerate() {
        if i as u16 >= log_inner.height {
            break;
        }
        screen
            .current
            .set_str(log_inner.x, log_inner.y + i as u16, line, Style::default());
    }
}

fn render_footer(screen: &mut Screen, area: Rect, app: &App) {
    let running = app.running_agent_count();

    // Status message takes priority
    if let Some((msg, is_error, _)) = &app.status_message {
        let color = if *is_error { Color::Red } else { Color::Green };
        screen
            .current
            .set_str(area.x + 1, area.y, msg, Style::default().fg(color));
        return;
    }

    // Keybinds
    render_keybinds(screen, area.x + 1, area.y);

    // Agent status on second line if running
    if running > 0 && area.height > 1 {
        let spinner = app.spinner();
        let status = format!(
            " {} {} agent{} running",
            spinner,
            running,
            if running > 1 { "s" } else { "" }
        );
        screen.current.set_str(
            area.x + 1,
            area.y + 1,
            &status,
            Style::default().fg(Color::Yellow),
        );
    }
}

fn render_keybinds(screen: &mut Screen, x: u16, y: u16) {
    let binds = [
        ("j/k", "Nav"),
        ("Enter", "Details"),
        ("f", "Fix"),
        ("F", "Agent"),
        ("c", "Cancel"),
        ("a", "Agents"),
        ("q", "Quit"),
    ];
    let colors = [
        Color::Cyan,
        Color::Cyan,
        Color::Yellow,
        Color::Green,
        Color::Red,
        Color::Magenta,
        Color::Cyan,
    ];

    let mut cx = x;
    for (i, (key, action)) in binds.iter().enumerate() {
        screen
            .current
            .set_str(cx, y, key, Style::default().fg(colors[i]).bold());
        cx += key.len() as u16;
        let sep = format!(":{} ", action);
        screen
            .current
            .set_str(cx, y, &sep, Style::default());
        cx += sep.len() as u16;
    }
}
