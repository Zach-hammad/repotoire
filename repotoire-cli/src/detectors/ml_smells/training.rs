use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::info;

use super::{
    backward_call, dataloader_shuffle, eval_mode, forward_method, nan_equality, torch_load,
    torch_load_weights_only, zero_grad_call,
};

pub struct TorchLoadUnsafeDetector {
    repository_path: PathBuf,
}

impl TorchLoadUnsafeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for TorchLoadUnsafeDetector {
    fn name(&self) -> &'static str {
        "torch-load-unsafe"
    }

    fn description(&self) -> &'static str {
        "Detects torch.load() without weights_only=True (pickle RCE)"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if torch_load().is_match(line) && !torch_load_weights_only().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "TorchLoadUnsafeDetector",
                                &file_str,
                                line_num,
                                "torch.load without weights_only",
                            ),
                            detector: "TorchLoadUnsafeDetector".to_string(),
                            severity: Severity::Critical,
                            title: "torch.load() without weights_only=True".to_string(),
                            description: "torch.load() uses pickle by default, which can execute \
                                arbitrary code during deserialization. Malicious model files can \
                                compromise your system.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Add weights_only=True:\n\
                                ```python\n\
                                model = torch.load('model.pth', weights_only=True)\n\
                                ```\n\n\
                                If you need full pickle (trusted source only):\n\
                                ```python\n\
                                model = torch.load('model.pth', weights_only=False)  # explicitly unsafe\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-502".to_string()),
                            why_it_matters: Some(
                                "Pickle deserialization can execute arbitrary code. Attackers can \
                                craft malicious .pth files that run code when loaded. This is a \
                                common supply chain attack vector for ML models.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("TorchLoadUnsafeDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// NanEqualityDetector
// ============================================================================

/// Detects comparisons with NaN (always False due to IEEE 754)
pub struct NanEqualityDetector {
    repository_path: PathBuf,
}

impl NanEqualityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for NanEqualityDetector {
    fn name(&self) -> &'static str {
        "nan-equality"
    }

    fn description(&self) -> &'static str {
        "Detects comparisons with NaN (always False)"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if nan_equality().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "NanEqualityDetector",
                                &file_str,
                                line_num,
                                "NaN equality comparison",
                            ),
                            detector: "NanEqualityDetector".to_string(),
                            severity: Severity::High,
                            title: "NaN equality comparison (always False)".to_string(),
                            description: "Comparing values with NaN using == or != always returns \
                                False due to IEEE 754. NaN != NaN by definition.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Use dedicated NaN-checking functions:\n\
                                ```python\n\
                                # NumPy\n\
                                np.isnan(x)\n\n\
                                # Pandas\n\
                                pd.isna(x) or pd.isnull(x)\n\n\
                                # Python math\n\
                                math.isnan(x)\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("correctness".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "This is a logic bug. Code like `if x == np.nan` will never execute \
                                the true branch, even when x is NaN. This causes silent data corruption \
                                in data pipelines.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("NanEqualityDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// MissingZeroGradDetector
// ============================================================================

/// Detects .backward() without zero_grad() in training loops
pub struct MissingZeroGradDetector {
    repository_path: PathBuf,
}

impl MissingZeroGradDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }

    /// Check if a file has both backward() and zero_grad()
    fn analyze_file(&self, content: &str, path: &std::path::Path) -> Vec<Finding> {
        let mut findings = vec![];
        let has_backward = backward_call().is_match(content);
        let has_zero_grad = zero_grad_call().is_match(content);

        if has_backward && !has_zero_grad {
            // Find the line with backward()
            let lines: Vec<&str> = content.lines().collect();
            for (i, line) in lines.iter().enumerate() {
                let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                if backward_call().is_match(line) {
                    let file_str = path.to_string_lossy();
                    let line_num = (i + 1) as u32;

                    findings.push(Finding {
                        id: deterministic_finding_id(
                            "MissingZeroGradDetector",
                            &file_str,
                            line_num,
                            "backward without zero_grad",
                        ),
                        detector: "MissingZeroGradDetector".to_string(),
                        severity: Severity::High,
                        title: ".backward() without zero_grad()".to_string(),
                        description: "Calling .backward() without clearing gradients causes \
                            gradient accumulation. Gradients from previous batches add up, \
                            leading to incorrect updates.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: Some(line_num),
                        suggested_fix: Some(
                            "Add optimizer.zero_grad() before backward():\n\
                            ```python\n\
                            optimizer.zero_grad()  # Clear gradients\n\
                            loss.backward()        # Compute gradients\n\
                            optimizer.step()       # Update weights\n\
                            ```".to_string()
                        ),
                        estimated_effort: Some("5 minutes".to_string()),
                        category: Some("correctness".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Without zero_grad(), gradients accumulate across batches. This causes \
                            training instability and incorrect weight updates. The model may fail \
                            to converge or produce wrong results.".to_string()
                        ),
                        ..Default::default()
                    });
                    break; // One finding per file
                }
            }
        }

        findings
    }
}

impl Detector for MissingZeroGradDetector {
    fn name(&self) -> &'static str {
        "missing-zero-grad"
    }

    fn description(&self) -> &'static str {
        "Detects .backward() without zero_grad()"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                findings.extend(self.analyze_file(&content, path));
            }
        }

        info!("MissingZeroGradDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// ForwardMethodDetector
// ============================================================================

/// Detects model.forward() instead of model() - skips hooks
pub struct ForwardMethodDetector {
    repository_path: PathBuf,
}

impl ForwardMethodDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for ForwardMethodDetector {
    fn name(&self) -> &'static str {
        "forward-method"
    }

    fn description(&self) -> &'static str {
        "Detects model.forward() instead of model()"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                // Skip files that don't use PyTorch
                if !content.contains("torch") && !content.contains("nn.Module") {
                    continue;
                }

                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Skip definitions of forward method
                    if line.contains("def forward") {
                        continue;
                    }

                    if forward_method().is_match(line) {
                        // Skip valid forward patterns (parent class, self, RPC pipelines)
                        if line.contains("super()")
                            || line.contains("super(")
                            || line.contains("self.forward")
                            || line.contains(".remote().forward")
                            || line.contains(".rpc_async().forward")
                            || line.contains(".rpc_sync().forward")
                        {
                            continue;
                        }

                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "ForwardMethodDetector",
                                &file_str,
                                line_num,
                                "direct forward() call",
                            ),
                            detector: "ForwardMethodDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Direct .forward() call instead of model()".to_string(),
                            description: "Calling model.forward() directly bypasses hooks \
                                (forward_pre_hooks, forward_hooks). Use model() instead."
                                .to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Call the model directly:\n\
                                ```python\n\
                                # Instead of:\n\
                                output = model.forward(x)\n\n\
                                # Use:\n\
                                output = model(x)\n\
                                ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("best-practice".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "PyTorch hooks (for debugging, profiling, gradient modification) \
                                are only triggered when calling model() via __call__. Direct \
                                forward() calls skip these hooks, breaking tools like SHAP, \
                                GradCAM, and profilers."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("ForwardMethodDetector found {} findings", findings.len());
        Ok(findings)
    }
}
