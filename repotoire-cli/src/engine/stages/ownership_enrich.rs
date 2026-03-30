//! Stage 4.5: Ownership enrichment (pure — reads git history, produces OwnershipModel).

use crate::git::ownership::{compute_ownership_model, OwnershipConfig, OwnershipModel};
use anyhow::Result;
use std::path::Path;

/// Input for the ownership enrichment stage.
pub struct OwnershipEnrichInput<'a> {
    pub repo_path: &'a Path,
    pub ownership_config: OwnershipConfig,
}

/// Output from the ownership enrichment stage.
pub struct OwnershipEnrichOutput {
    pub model: OwnershipModel,
}

/// Compute ownership model from git history.
pub fn ownership_enrich_stage(input: &OwnershipEnrichInput) -> Result<OwnershipEnrichOutput> {
    let history = match crate::git::history::GitHistory::open(input.repo_path) {
        Ok(h) => h,
        Err(_) => {
            tracing::debug!("Ownership analysis skipped: cannot open git repo");
            return Ok(OwnershipEnrichOutput {
                model: OwnershipModel::empty(),
            });
        }
    };

    let commits = match history.get_recent_commits(5000, None) {
        Ok(c) => c,
        Err(e) => {
            tracing::debug!("Ownership analysis skipped: {e}");
            return Ok(OwnershipEnrichOutput {
                model: OwnershipModel::empty(),
            });
        }
    };

    let model = compute_ownership_model(&commits, &input.ownership_config);

    Ok(OwnershipEnrichOutput { model })
}
