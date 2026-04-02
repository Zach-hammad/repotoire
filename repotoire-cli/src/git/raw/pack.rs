use std::path::Path;

use super::deflate::inflate_zlib;
use super::error::GitError;
use super::object::ObjectType;
use super::pack_index::PackIndex;

const MAX_DELTA_CHAIN: usize = 50;

/// Reader for git packfiles (.pack).
///
/// Layout: 12-byte header ("PACK" + version u32 + object count u32),
/// then N object entries, each with VLQ-encoded type+size header.
pub struct Packfile {
    data: Vec<u8>,
}

impl Packfile {
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, GitError> {
        let data = std::fs::read(path.as_ref()).map_err(GitError::Io)?;
        Self::validate_header(&data)?;
        Ok(Self { data })
    }

    pub fn from_bytes(data: &[u8]) -> Result<Self, GitError> {
        Self::validate_header(data)?;
        Ok(Self {
            data: data.to_vec(),
        })
    }

    fn validate_header(data: &[u8]) -> Result<(), GitError> {
        if data.len() < 12 {
            return Err(GitError::InvalidPack("pack file too small".into()));
        }
        if &data[..4] != b"PACK" {
            return Err(GitError::InvalidPack("bad pack magic".into()));
        }
        let version = u32::from_be_bytes([data[4], data[5], data[6], data[7]]);
        if version != 2 {
            return Err(GitError::InvalidPack(format!(
                "unsupported pack version: {version}"
            )));
        }
        Ok(())
    }

    /// Read and fully resolve an object at the given byte offset in the packfile.
    pub fn read_object_at(
        &self,
        offset: u64,
        idx: &PackIndex,
    ) -> Result<(ObjectType, Vec<u8>), GitError> {
        self.read_object_recursive(offset, idx, 0)
    }

    fn read_object_recursive(
        &self,
        offset: u64,
        idx: &PackIndex,
        depth: usize,
    ) -> Result<(ObjectType, Vec<u8>), GitError> {
        if depth > MAX_DELTA_CHAIN {
            return Err(GitError::DeltaChainTooDeep(MAX_DELTA_CHAIN));
        }

        let pos = offset as usize;
        let (type_id, _size, header_len) = parse_object_header(&self.data[pos..]);
        let data_start = pos + header_len;

        match type_id {
            // Commit, Tree, Blob, Tag — inflate raw deflate
            1..=4 => {
                let obj_type = ObjectType::from_type_id(type_id)?;
                let content = inflate_zlib(&self.data[data_start..])?;
                Ok((obj_type, content))
            }
            // OFS_DELTA
            6 => {
                let (neg_offset, ofs_len) = read_ofs_delta_offset(&self.data[data_start..]);
                let base_offset = offset
                    .checked_sub(neg_offset)
                    .ok_or_else(|| GitError::InvalidPack("OFS_DELTA underflow".into()))?;
                let delta_data_start = data_start + ofs_len;
                let delta_bytes = inflate_zlib(&self.data[delta_data_start..])?;
                let (base_type, base_data) =
                    self.read_object_recursive(base_offset, idx, depth + 1)?;
                let result = apply_delta(&base_data, &delta_bytes)?;
                Ok((base_type, result))
            }
            // REF_DELTA
            7 => {
                let ref_oid_bytes: [u8; 20] = self.data[data_start..data_start + 20]
                    .try_into()
                    .map_err(|_| GitError::InvalidPack("REF_DELTA OID too short".into()))?;
                let ref_oid = super::oid::Oid::from_bytes(ref_oid_bytes);
                let delta_data_start = data_start + 20;
                let delta_bytes = inflate_zlib(&self.data[delta_data_start..])?;

                let base_offset = idx
                    .find(&ref_oid)
                    .ok_or(GitError::ObjectNotFound(ref_oid))?;
                let (base_type, base_data) =
                    self.read_object_recursive(base_offset, idx, depth + 1)?;
                let result = apply_delta(&base_data, &delta_bytes)?;
                Ok((base_type, result))
            }
            _ => Err(GitError::InvalidPack(format!(
                "unknown pack object type: {type_id}"
            ))),
        }
    }
}

/// Parse VLQ-encoded object header: type (3 bits) + size (variable).
/// Returns (type_id, size, bytes_consumed).
pub fn parse_object_header(data: &[u8]) -> (u8, u64, usize) {
    let byte = data[0];
    let type_id = (byte >> 4) & 0x07;
    let mut size = (byte & 0x0F) as u64;
    let mut shift = 4u32;
    let mut pos = 1;

    if byte & 0x80 != 0 {
        loop {
            let byte = data[pos];
            size |= ((byte & 0x7F) as u64) << shift;
            shift += 7;
            pos += 1;
            if byte & 0x80 == 0 {
                break;
            }
        }
    }

    (type_id, size, pos)
}

/// Read OFS_DELTA negative offset using non-standard VLQ with +1 correction.
fn read_ofs_delta_offset(data: &[u8]) -> (u64, usize) {
    let mut result = (data[0] & 0x7F) as u64;
    let mut pos = 1;

    if data[0] & 0x80 != 0 {
        loop {
            let byte = data[pos];
            result = (result + 1) << 7;
            result |= (byte & 0x7F) as u64;
            pos += 1;
            if byte & 0x80 == 0 {
                break;
            }
        }
    }

    (result, pos)
}

/// Apply a git delta instruction stream to a base object.
fn apply_delta(base: &[u8], delta: &[u8]) -> Result<Vec<u8>, GitError> {
    let mut pos = 0;

    // Read base size VLQ
    let (_base_size, consumed) = read_size_vlq(&delta[pos..]);
    pos += consumed;

    // Read result size VLQ
    let (result_size, consumed) = read_size_vlq(&delta[pos..]);
    pos += consumed;

    let mut output = Vec::with_capacity(result_size as usize);

    while pos < delta.len() {
        let cmd = delta[pos];
        pos += 1;

        if cmd & 0x80 != 0 {
            // Copy instruction
            let mut offset = 0u32;
            let mut size = 0u32;

            if cmd & 0x01 != 0 {
                offset |= delta[pos] as u32;
                pos += 1;
            }
            if cmd & 0x02 != 0 {
                offset |= (delta[pos] as u32) << 8;
                pos += 1;
            }
            if cmd & 0x04 != 0 {
                offset |= (delta[pos] as u32) << 16;
                pos += 1;
            }
            if cmd & 0x08 != 0 {
                offset |= (delta[pos] as u32) << 24;
                pos += 1;
            }

            if cmd & 0x10 != 0 {
                size |= delta[pos] as u32;
                pos += 1;
            }
            if cmd & 0x20 != 0 {
                size |= (delta[pos] as u32) << 8;
                pos += 1;
            }
            if cmd & 0x40 != 0 {
                size |= (delta[pos] as u32) << 16;
                pos += 1;
            }

            // size=0 means 0x10000
            if size == 0 {
                size = 0x10000;
            }

            let start = offset as usize;
            let end = start + size as usize;
            if end > base.len() {
                return Err(GitError::InvalidPack(format!(
                    "delta copy out of bounds: offset={offset} size={size} base_len={}",
                    base.len()
                )));
            }
            output.extend_from_slice(&base[start..end]);
        } else if cmd > 0 {
            // Insert instruction: copy `cmd` literal bytes from delta
            let len = cmd as usize;
            output.extend_from_slice(&delta[pos..pos + len]);
            pos += len;
        } else {
            // cmd == 0 is reserved/invalid
            return Err(GitError::InvalidPack(
                "delta instruction 0 is reserved".into(),
            ));
        }
    }

    if output.len() != result_size as usize {
        return Err(GitError::InvalidPack(format!(
            "delta result size mismatch: expected {result_size}, got {}",
            output.len()
        )));
    }

    Ok(output)
}

/// Read a size VLQ (used in delta headers, different from object header VLQ).
fn read_size_vlq(data: &[u8]) -> (u64, usize) {
    let mut result = 0u64;
    let mut shift = 0u32;
    let mut pos = 0;

    loop {
        let byte = data[pos];
        result |= ((byte & 0x7F) as u64) << shift;
        shift += 7;
        pos += 1;
        if byte & 0x80 == 0 {
            break;
        }
    }

    (result, pos)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_object_from_pack() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let pack_dir = git_dir.join("objects/pack");
        if !pack_dir.exists() {
            return;
        }

        for entry in std::fs::read_dir(&pack_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".idx") {
                let pack_path = entry.path().with_extension("pack");
                let idx = PackIndex::open(entry.path()).unwrap();
                let pack = Packfile::open(&pack_path).unwrap();

                let oid = idx.oid_at(0).unwrap();
                let offset = idx.find(&oid).unwrap();
                let (obj_type, data) = pack.read_object_at(offset, &idx).unwrap();
                assert!(matches!(
                    obj_type,
                    ObjectType::Commit | ObjectType::Tree | ObjectType::Blob | ObjectType::Tag
                ));
                assert!(!data.is_empty());
                return;
            }
        }
    }

    #[test]
    fn test_pack_header_validation() {
        let bad_data = b"NOTPACK";
        let result = Packfile::from_bytes(bad_data);
        assert!(result.is_err());
    }

    #[test]
    fn test_vlq_size_decode() {
        // Type 1 (commit), size 10: byte = (0 << 7) | (1 << 4) | 10 = 0x1A
        let data = [0x1A];
        let (obj_type, size, consumed) = parse_object_header(&data);
        assert_eq!(obj_type, 1);
        assert_eq!(size, 10);
        assert_eq!(consumed, 1);
    }
}
