use std::path::Path;

use super::error::GitError;
use super::oid::Oid;

/// Reader for pack index v2 files (.idx).
///
/// Layout (all integers big-endian):
///   8 bytes: magic (0xff744f63) + version (2)
///   256 x u32: fanout table (cumulative count of OIDs with first byte <= i)
///   N x 20 bytes: sorted OID table
///   N x u32: CRC32 checksums (unused by us)
///   N x u32: 32-bit offsets (MSB set → index into 64-bit table)
///   M x u64: 64-bit offsets (for packs > 4GB)
pub struct PackIndex {
    data: Vec<u8>,
    object_count: u32,
}

const HEADER_SIZE: usize = 8;
const FANOUT_SIZE: usize = 256 * 4;
const FANOUT_OFFSET: usize = HEADER_SIZE;
const OID_TABLE_OFFSET: usize = HEADER_SIZE + FANOUT_SIZE;

impl PackIndex {
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, GitError> {
        let data = std::fs::read(path.as_ref()).map_err(GitError::Io)?;

        if data.len() < HEADER_SIZE + FANOUT_SIZE {
            return Err(GitError::InvalidPack("index file too small".into()));
        }

        // Verify magic + version
        let magic = read_u32_be(&data, 0);
        let version = read_u32_be(&data, 4);
        if magic != 0xff74_4f63 {
            return Err(GitError::InvalidPack(format!(
                "bad pack index magic: {magic:#010x}"
            )));
        }
        if version != 2 {
            return Err(GitError::InvalidPack(format!(
                "unsupported pack index version: {version}"
            )));
        }

        let object_count = read_u32_be(&data, FANOUT_OFFSET + 255 * 4);

        Ok(Self {
            data,
            object_count,
        })
    }

    pub fn object_count(&self) -> u32 {
        self.object_count
    }

    /// Look up an OID in the index, returning its pack file offset.
    pub fn find(&self, oid: &Oid) -> Option<u64> {
        let bytes = oid.as_bytes();
        let first_byte = bytes[0] as usize;

        // Fanout gives us the search range
        let lo = if first_byte == 0 {
            0u32
        } else {
            read_u32_be(&self.data, FANOUT_OFFSET + (first_byte - 1) * 4)
        };
        let hi = read_u32_be(&self.data, FANOUT_OFFSET + first_byte * 4);

        if lo >= hi {
            return None;
        }

        // Binary search in OID table
        let mut low = lo;
        let mut high = hi;
        while low < high {
            let mid = low + (high - low) / 2;
            let oid_offset = OID_TABLE_OFFSET + mid as usize * 20;
            let entry = &self.data[oid_offset..oid_offset + 20];

            match entry.cmp(bytes.as_slice()) {
                std::cmp::Ordering::Equal => {
                    return Some(self.read_offset(mid));
                }
                std::cmp::Ordering::Less => {
                    low = mid + 1;
                }
                std::cmp::Ordering::Greater => {
                    high = mid;
                }
            }
        }

        None
    }

    /// Get the OID at a given sorted index.
    pub fn oid_at(&self, index: u32) -> Option<Oid> {
        if index >= self.object_count {
            return None;
        }
        let offset = OID_TABLE_OFFSET + index as usize * 20;
        Oid::from_slice(&self.data[offset..offset + 20]).ok()
    }

    /// Read the pack offset for an entry at the given index.
    fn read_offset(&self, index: u32) -> u64 {
        let n = self.object_count as usize;
        // Offset table starts after: OID table (N*20) + CRC32 table (N*4)
        let offset_table_start = OID_TABLE_OFFSET + n * 20 + n * 4;
        let raw = read_u32_be(&self.data, offset_table_start + index as usize * 4);

        if raw & 0x8000_0000 != 0 {
            // MSB set: index into 64-bit offset table
            let large_idx = (raw & 0x7FFF_FFFF) as usize;
            let large_table_start = offset_table_start + n * 4;
            read_u64_be(&self.data, large_table_start + large_idx * 8)
        } else {
            raw as u64
        }
    }
}

fn read_u32_be(data: &[u8], offset: usize) -> u32 {
    u32::from_be_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}

fn read_u64_be(data: &[u8], offset: usize) -> u64 {
    u64::from_be_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
        data[offset + 4],
        data[offset + 5],
        data[offset + 6],
        data[offset + 7],
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_real_pack_index() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let pack_dir = git_dir.join("objects/pack");
        if !pack_dir.exists() {
            return;
        }

        for entry in std::fs::read_dir(&pack_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".idx") {
                let idx = PackIndex::open(entry.path()).unwrap();
                assert!(idx.object_count() > 0);

                let first_oid = idx.oid_at(0).unwrap();
                let offset = idx.find(&first_oid);
                assert!(offset.is_some(), "first OID not found in index");
                return;
            }
        }
    }

    #[test]
    fn test_binary_search_correctness() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let pack_dir = git_dir.join("objects/pack");
        if !pack_dir.exists() {
            return;
        }

        for entry in std::fs::read_dir(&pack_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".idx") {
                let idx = PackIndex::open(entry.path()).unwrap();
                let count = idx.object_count();
                for i in [0, count / 4, count / 2, count - 1].iter().copied() {
                    if i >= count {
                        continue;
                    }
                    let oid = idx.oid_at(i).unwrap();
                    assert!(idx.find(&oid).is_some(), "OID at index {i} not found");
                }
                // Non-existent OID
                assert!(idx.find(&Oid::ZERO).is_none());
                return;
            }
        }
    }
}
