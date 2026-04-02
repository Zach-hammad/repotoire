//! RFC 3174 SHA-1 implementation (hash-only, not for cryptographic use).
//! Used exclusively for git object ID computation.

const H0: u32 = 0x67452301;
const H1: u32 = 0xEFCDAB89;
const H2: u32 = 0x98BADCFE;
const H3: u32 = 0x10325476;
const H4: u32 = 0xC3D2E1F0;

const K0: u32 = 0x5A827999;
const K1: u32 = 0x6ED9EBA1;
const K2: u32 = 0x8F1BBCDC;
const K3: u32 = 0xCA62C1D6;

pub struct Sha1 {
    state: [u32; 5],
    buffer: [u8; 64],
    buf_len: usize,
    total_len: u64,
}

impl Default for Sha1 {
    fn default() -> Self {
        Self {
            state: [H0, H1, H2, H3, H4],
            buffer: [0u8; 64],
            buf_len: 0,
            total_len: 0,
        }
    }
}

impl Sha1 {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn update(&mut self, data: &[u8]) {
        self.total_len += data.len() as u64;
        let mut offset = 0;

        // Fill buffer if partially full
        if self.buf_len > 0 {
            let space = 64 - self.buf_len;
            let copy = space.min(data.len());
            self.buffer[self.buf_len..self.buf_len + copy].copy_from_slice(&data[..copy]);
            self.buf_len += copy;
            offset = copy;

            if self.buf_len == 64 {
                let block: [u8; 64] = self.buffer;
                self.transform(&block);
                self.buf_len = 0;
            }
        }

        // Process complete 64-byte blocks
        while offset + 64 <= data.len() {
            let block: [u8; 64] = data[offset..offset + 64]
                .try_into()
                .expect("slice is exactly 64 bytes");
            self.transform(&block);
            offset += 64;
        }

        // Buffer remaining bytes
        let remaining = data.len() - offset;
        if remaining > 0 {
            self.buffer[..remaining].copy_from_slice(&data[offset..]);
            self.buf_len = remaining;
        }
    }

    pub fn finalize(mut self) -> [u8; 20] {
        let bit_len = self.total_len * 8;

        // Append 0x80
        self.buffer[self.buf_len] = 0x80;
        self.buf_len += 1;

        // If not enough room for 8-byte length, pad and process
        if self.buf_len > 56 {
            for i in self.buf_len..64 {
                self.buffer[i] = 0;
            }
            let block: [u8; 64] = self.buffer;
            self.transform(&block);
            self.buf_len = 0;
        }

        // Zero-pad to 56 bytes, append 8-byte big-endian bit length
        for i in self.buf_len..56 {
            self.buffer[i] = 0;
        }
        self.buffer[56..64].copy_from_slice(&bit_len.to_be_bytes());
        let block: [u8; 64] = self.buffer;
        self.transform(&block);

        // Emit state as big-endian bytes
        let mut out = [0u8; 20];
        for (i, &word) in self.state.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }

    fn transform(&mut self, block: &[u8; 64]) {
        // Message schedule
        let mut w = [0u32; 80];
        for t in 0..16 {
            w[t] = u32::from_be_bytes([
                block[t * 4],
                block[t * 4 + 1],
                block[t * 4 + 2],
                block[t * 4 + 3],
            ]);
        }
        for t in 16..80 {
            w[t] = (w[t - 3] ^ w[t - 8] ^ w[t - 14] ^ w[t - 16]).rotate_left(1);
        }

        let [mut a, mut b, mut c, mut d, mut e] = self.state;

        for t in 0..80 {
            let (f, k) = match t {
                0..=19 => ((b & c) | ((!b) & d), K0),
                20..=39 => (b ^ c ^ d, K1),
                40..=59 => ((b & c) | (b & d) | (c & d), K2),
                _ => (b ^ c ^ d, K3),
            };

            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(w[t]);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }

        self.state[0] = self.state[0].wrapping_add(a);
        self.state[1] = self.state[1].wrapping_add(b);
        self.state[2] = self.state[2].wrapping_add(c);
        self.state[3] = self.state[3].wrapping_add(d);
        self.state[4] = self.state[4].wrapping_add(e);
    }
}

/// Convenience: hash a single byte slice.
pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h = Sha1::new();
    h.update(data);
    h.finalize()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty() {
        let hash = sha1(b"");
        assert_eq!(hex(&hash), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
    }

    #[test]
    fn test_abc() {
        let hash = sha1(b"abc");
        assert_eq!(hex(&hash), "a9993e364706816aba3e25717850c26c9cd0d89d");
    }

    #[test]
    fn test_long() {
        let hash = sha1(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq");
        assert_eq!(hex(&hash), "84983e441c3bd26ebaae4aa1f95129e5e54670f1");
    }

    #[test]
    fn test_million_a() {
        let data = vec![b'a'; 1_000_000];
        let hash = sha1(&data);
        assert_eq!(hex(&hash), "34aa973cd4c4daa4f61eeb2bdbad27316534016f");
    }

    #[test]
    fn test_streaming() {
        let mut hasher = Sha1::new();
        hasher.update(b"abc");
        hasher.update(b"dbcdecdefdefg");
        hasher.update(b"efghfghighijhijkijkljklmklmnlmnomnopnopq");
        let hash = hasher.finalize();
        assert_eq!(hex(&hash), "84983e441c3bd26ebaae4aa1f95129e5e54670f1");
    }

    #[test]
    fn test_git_blob_hash() {
        // git hash-object computes: SHA1("blob 5\0hello")
        let content = b"hello";
        let header = format!("blob {}\0", content.len());
        let mut hasher = Sha1::new();
        hasher.update(header.as_bytes());
        hasher.update(content);
        let hash = hasher.finalize();
        assert_eq!(hex(&hash), "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0");
    }

    fn hex(bytes: &[u8; 20]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }
}
