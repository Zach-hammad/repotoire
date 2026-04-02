use std::fmt;

use super::error::GitError;

#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Oid([u8; 20]);

impl Oid {
    pub const ZERO: Oid = Oid([0; 20]);

    pub fn from_hex(hex: &str) -> Result<Self, GitError> {
        if hex.len() != 40 {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("invalid OID hex length: {}", hex.len()),
            });
        }
        let mut bytes = [0u8; 20];
        for i in 0..20 {
            bytes[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).map_err(|_| {
                GitError::CorruptObject {
                    path: String::new(),
                    detail: format!("invalid hex in OID: {hex}"),
                }
            })?;
        }
        Ok(Oid(bytes))
    }

    pub fn from_bytes(bytes: [u8; 20]) -> Self {
        Oid(bytes)
    }

    pub fn as_bytes(&self) -> &[u8; 20] {
        &self.0
    }

    pub fn to_hex(&self) -> String {
        let mut s = String::with_capacity(40);
        for b in &self.0 {
            s.push_str(&format!("{b:02x}"));
        }
        s
    }

    /// Read 20 raw bytes from a slice (e.g., tree entry, pack index).
    pub fn from_slice(data: &[u8]) -> Result<Self, GitError> {
        if data.len() < 20 {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: "OID slice too short".into(),
            });
        }
        let mut bytes = [0u8; 20];
        bytes.copy_from_slice(&data[..20]);
        Ok(Oid(bytes))
    }
}

/// Display prints short hash (first 12 hex chars).
impl fmt::Display for Oid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for b in &self.0[..6] {
            write!(f, "{b:02x}")?;
        }
        Ok(())
    }
}

impl fmt::Debug for Oid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Oid({})", self.to_hex())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_from_hex_valid() {
        let hex = "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3";
        let oid = Oid::from_hex(hex).unwrap();
        assert_eq!(oid.to_hex(), hex);
    }

    #[test]
    fn test_from_hex_invalid_length() {
        assert!(Oid::from_hex("abcd").is_err());
    }

    #[test]
    fn test_from_hex_invalid_chars() {
        let bad = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";
        assert!(Oid::from_hex(bad).is_err());
    }

    #[test]
    fn test_from_bytes_roundtrip() {
        let bytes = [
            0xA9, 0x4A, 0x8F, 0xE5, 0xCC, 0xB1, 0x9B, 0xA6, 0x1C, 0x4C, 0x08, 0x73, 0xD3,
            0x91, 0xE9, 0x87, 0x98, 0x2F, 0xBB, 0xD3,
        ];
        let oid = Oid::from_bytes(bytes);
        assert_eq!(oid.as_bytes(), &bytes);
        assert_eq!(oid.to_hex(), "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3");
    }

    #[test]
    fn test_display_short_hash() {
        let hex = "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3";
        let oid = Oid::from_hex(hex).unwrap();
        assert_eq!(format!("{oid}"), "a94a8fe5ccb1");
    }

    #[test]
    fn test_ord() {
        let a = Oid::from_hex("0000000000000000000000000000000000000001").unwrap();
        let b = Oid::from_hex("0000000000000000000000000000000000000002").unwrap();
        assert!(a < b);
    }
}
