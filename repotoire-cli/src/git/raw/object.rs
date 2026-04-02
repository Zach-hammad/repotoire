use std::path::Path;

use super::deflate::inflate_zlib;
use super::error::GitError;
use super::oid::Oid;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObjectType {
    Commit,
    Tree,
    Blob,
    Tag,
}

impl ObjectType {
    pub fn parse(s: &str) -> Result<Self, GitError> {
        match s {
            "commit" => Ok(Self::Commit),
            "tree" => Ok(Self::Tree),
            "blob" => Ok(Self::Blob),
            "tag" => Ok(Self::Tag),
            _ => Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("unknown object type: {s}"),
            }),
        }
    }

    pub fn type_id(&self) -> u8 {
        match self {
            Self::Commit => 1,
            Self::Tree => 2,
            Self::Blob => 3,
            Self::Tag => 4,
        }
    }

    pub fn from_type_id(id: u8) -> Result<Self, GitError> {
        match id {
            1 => Ok(Self::Commit),
            2 => Ok(Self::Tree),
            3 => Ok(Self::Blob),
            4 => Ok(Self::Tag),
            _ => Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("unknown object type id: {id}"),
            }),
        }
    }
}

/// Parse the "type size\0content" format from decompressed object data.
pub fn parse_object_data(data: &[u8]) -> Result<(ObjectType, &[u8]), GitError> {
    let nul_pos = data.iter().position(|&b| b == 0).ok_or_else(|| {
        GitError::CorruptObject {
            path: String::new(),
            detail: "no NUL in object header".into(),
        }
    })?;
    let header = std::str::from_utf8(&data[..nul_pos]).map_err(|_| GitError::CorruptObject {
        path: String::new(),
        detail: "non-UTF8 object header".into(),
    })?;
    let space_pos = header.find(' ').ok_or_else(|| GitError::CorruptObject {
        path: String::new(),
        detail: "no space in object header".into(),
    })?;
    let obj_type = ObjectType::parse(&header[..space_pos])?;
    let content = &data[nul_pos + 1..];
    Ok((obj_type, content))
}

/// Read and decompress a loose object from the objects directory.
pub fn read_loose_object(
    objects_dir: &Path,
    oid: &Oid,
) -> Result<(ObjectType, Vec<u8>), GitError> {
    let hex = oid.to_hex();
    let path = objects_dir.join(&hex[..2]).join(&hex[2..]);
    let compressed = std::fs::read(&path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            GitError::ObjectNotFound(*oid)
        } else {
            GitError::Io(e)
        }
    })?;
    let decompressed = inflate_zlib(&compressed)?;
    let (obj_type, content) = parse_object_data(&decompressed)?;
    Ok((obj_type, content.to_vec()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_object_header_blob() {
        let data = b"blob 5\0hello";
        let (obj_type, content) = parse_object_data(data).unwrap();
        assert_eq!(obj_type, ObjectType::Blob);
        assert_eq!(content, b"hello");
    }

    #[test]
    fn test_parse_object_header_commit() {
        let data = b"commit 11\0tree abcdef";
        let (obj_type, content) = parse_object_data(data).unwrap();
        assert_eq!(obj_type, ObjectType::Commit);
        assert_eq!(content, b"tree abcdef");
    }

    #[test]
    fn test_read_real_loose_object() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let objects_dir = git_dir.join("objects");
        for entry in std::fs::read_dir(&objects_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.len() == 2 && entry.path().is_dir() && name != "pa" && name != "in" {
                for file in std::fs::read_dir(entry.path()).unwrap().flatten() {
                    let fname = file.file_name().to_string_lossy().to_string();
                    let hex = format!("{name}{fname}");
                    if hex.len() != 40 {
                        continue;
                    }
                    let oid = Oid::from_hex(&hex).unwrap();
                    let (obj_type, _content) = read_loose_object(&objects_dir, &oid).unwrap();
                    assert!(matches!(
                        obj_type,
                        ObjectType::Blob | ObjectType::Tree | ObjectType::Commit | ObjectType::Tag
                    ));
                    return;
                }
            }
        }
        panic!("no loose objects found");
    }
}
