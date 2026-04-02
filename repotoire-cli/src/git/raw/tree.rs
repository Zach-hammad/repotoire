use super::error::GitError;
use super::oid::Oid;

#[derive(Debug, Clone)]
pub struct TreeEntry {
    pub mode: u32,
    pub name: String,
    pub oid: Oid,
}

impl TreeEntry {
    /// Is this entry a subtree (directory)?
    pub fn is_tree(&self) -> bool {
        self.mode == 0o40000
    }

    /// Is this entry a submodule (gitlink)?
    pub fn is_submodule(&self) -> bool {
        self.mode == 0o160000
    }
}

/// Parse raw tree object data into entries.
///
/// Format: repeated entries of "mode name\0" + 20 raw OID bytes.
/// Mode is variable-length octal ASCII (e.g., "40000" not "040000").
pub fn parse_tree(data: &[u8]) -> Result<Vec<TreeEntry>, GitError> {
    let mut entries = Vec::new();
    let mut pos = 0;

    while pos < data.len() {
        // Find space separating mode from name
        let space = data[pos..]
            .iter()
            .position(|&b| b == b' ')
            .ok_or_else(|| GitError::CorruptObject {
                path: String::new(),
                detail: "no space in tree entry".into(),
            })?;

        let mode_str = std::str::from_utf8(&data[pos..pos + space]).map_err(|_| {
            GitError::CorruptObject {
                path: String::new(),
                detail: "non-ASCII mode in tree entry".into(),
            }
        })?;
        let mode =
            u32::from_str_radix(mode_str, 8).map_err(|_| GitError::CorruptObject {
                path: String::new(),
                detail: format!("invalid octal mode: {mode_str}"),
            })?;

        pos += space + 1; // skip past space

        // Find NUL terminating the name
        let nul = data[pos..]
            .iter()
            .position(|&b| b == 0)
            .ok_or_else(|| GitError::CorruptObject {
                path: String::new(),
                detail: "no NUL after tree entry name".into(),
            })?;

        let name = std::str::from_utf8(&data[pos..pos + nul])
            .map_err(|_| GitError::CorruptObject {
                path: String::new(),
                detail: "non-UTF8 tree entry name".into(),
            })?
            .to_string();

        pos += nul + 1; // skip past NUL

        // Read 20-byte OID
        if pos + 20 > data.len() {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: "truncated OID in tree entry".into(),
            });
        }
        let oid = Oid::from_slice(&data[pos..pos + 20])?;
        pos += 20;

        entries.push(TreeEntry { mode, name, oid });
    }

    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_tree_entry() {
        let mut data = Vec::new();
        data.extend_from_slice(b"100644 hello.txt\0");
        data.extend_from_slice(&[0xAA; 20]);
        data.extend_from_slice(b"40000 subdir\0");
        data.extend_from_slice(&[0xBB; 20]);

        let entries = parse_tree(&data).unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].mode, 0o100644);
        assert_eq!(entries[0].name, "hello.txt");
        assert!(!entries[0].is_tree());
        assert_eq!(entries[1].mode, 0o40000);
        assert_eq!(entries[1].name, "subdir");
        assert!(entries[1].is_tree());
    }

    #[test]
    fn test_parse_submodule_entry() {
        let mut data = Vec::new();
        data.extend_from_slice(b"160000 external\0");
        data.extend_from_slice(&[0xCC; 20]);

        let entries = parse_tree(&data).unwrap();
        assert_eq!(entries.len(), 1);
        assert!(entries[0].is_submodule());
    }
}
