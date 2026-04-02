//! RFC 1951 DEFLATE decompressor + RFC 1950 zlib framing.
//! Used exclusively for decompressing git loose objects and packfile entries.

use super::error::GitError;

// ── Bit reader ──────────────────────────────────────────────────────────────

struct BitReader<'a> {
    data: &'a [u8],
    pos: usize,
    bits: u64,
    nbits: u8,
}

impl<'a> BitReader<'a> {
    fn new(data: &'a [u8]) -> Self {
        Self {
            data,
            pos: 0,
            bits: 0,
            nbits: 0,
        }
    }

    fn refill(&mut self) {
        while self.nbits <= 56 && self.pos < self.data.len() {
            self.bits |= (self.data[self.pos] as u64) << self.nbits;
            self.pos += 1;
            self.nbits += 8;
        }
    }

    fn peek(&mut self, n: u8) -> u32 {
        if self.nbits < n {
            self.refill();
        }
        (self.bits & ((1u64 << n) - 1)) as u32
    }

    fn consume(&mut self, n: u8) {
        self.bits >>= n;
        self.nbits -= n;
    }

    fn read_bits(&mut self, n: u8) -> u32 {
        let val = self.peek(n);
        self.consume(n);
        val
    }

    fn read_byte(&mut self) -> u8 {
        self.read_bits(8) as u8
    }

    fn align_to_byte(&mut self) {
        let discard = self.nbits % 8;
        if discard > 0 {
            self.consume(discard);
        }
    }

    fn read_u16_le(&mut self) -> u16 {
        self.align_to_byte();
        let lo = self.read_byte() as u16;
        let hi = self.read_byte() as u16;
        lo | (hi << 8)
    }

    /// How many input bytes have been consumed (approximate, for callers needing offset).
    fn bytes_consumed(&self) -> usize {
        self.pos - (self.nbits / 8) as usize
    }
}

// ── Huffman table ───────────────────────────────────────────────────────────

struct HuffmanTable {
    /// Primary lookup table (9-bit, covers most common codes).
    fast: Vec<(u16, u8)>, // (symbol, code_length) — 512 entries
    /// For codes longer than 9 bits, fall back to linear search.
    counts: [u16; 16],
    symbols: Vec<u16>,
    max_len: u8,
}

const FAST_BITS: u8 = 9;
const FAST_SIZE: usize = 1 << FAST_BITS;

impl HuffmanTable {
    fn build(lengths: &[u8]) -> Result<Self, GitError> {
        let max_len = *lengths.iter().max().unwrap_or(&0);

        let mut counts = [0u16; 16];
        for &len in lengths {
            if len > 0 {
                counts[len as usize] += 1;
            }
        }

        // Compute first code for each length
        let mut next_code = [0u32; 16];
        {
            let mut code = 0u32;
            for bits in 1..=15 {
                code = (code + counts[bits - 1] as u32) << 1;
                next_code[bits] = code;
            }
        }

        // Build sorted symbol table
        let num_symbols: usize = counts.iter().map(|&c| c as usize).sum();
        let mut symbols = vec![0u16; num_symbols];
        let mut offsets = [0u16; 16];
        {
            let mut off = 0u16;
            for bits in 1..=15 {
                offsets[bits] = off;
                off += counts[bits];
            }
        }

        // Assign codes and build fast table
        let mut fast = vec![(0u16, 0u8); FAST_SIZE];
        let mut codes = vec![0u32; lengths.len()];

        for (sym, &len) in lengths.iter().enumerate() {
            if len == 0 {
                continue;
            }
            let code = next_code[len as usize];
            next_code[len as usize] += 1;
            codes[sym] = code;

            let idx = offsets[len as usize] as usize;
            offsets[len as usize] += 1;
            symbols[idx] = sym as u16;

            // Populate fast lookup table
            if len <= FAST_BITS {
                // Reverse the bits for the fast table (DEFLATE reads LSB first)
                let reversed = reverse_bits(code, len);
                let fill = 1u32 << len;
                let mut entry = reversed;
                while entry < FAST_SIZE as u32 {
                    fast[entry as usize] = (sym as u16, len);
                    entry += fill;
                }
            }
        }

        // Rebuild offsets for slow decode
        {
            let mut off = 0u16;
            for bits in 1..=15 {
                offsets[bits] = off;
                off += counts[bits];
            }
        }

        // Re-sort symbols for slow path (by code length, then code value)
        let mut sorted_syms: Vec<(u8, u32, u16)> = lengths
            .iter()
            .enumerate()
            .filter(|(_, &len)| len > 0)
            .map(|(sym, &len)| (len, codes[sym], sym as u16))
            .collect();
        sorted_syms.sort();
        let symbols: Vec<u16> = sorted_syms.iter().map(|&(_, _, sym)| sym).collect();

        Ok(Self {
            fast,
            counts,
            symbols,
            max_len,
        })
    }

    fn decode(&self, reader: &mut BitReader) -> Result<u16, GitError> {
        reader.refill();

        // Fast path: check 9-bit lookup
        let peek = (reader.bits & ((1u64 << FAST_BITS) - 1)) as usize;
        let (sym, len) = self.fast[peek];
        if len > 0 {
            reader.consume(len);
            return Ok(sym);
        }

        // Slow path for codes > 9 bits
        let mut code = 0u32;
        let mut first = 0u32;
        let mut index = 0usize;

        for bits in 1..=self.max_len {
            // Read bit MSB-first (reversed: read LSB from stream, build code MSB-first)
            code |= ((reader.bits >> (bits - 1) as u64) & 1) as u32;

            let count = self.counts[bits as usize] as u32;
            if code < first + count {
                reader.consume(bits);
                return Ok(self.symbols[index + (code - first) as usize]);
            }
            index += count as usize;
            first = (first + count) << 1;
            code <<= 1;
        }

        Err(GitError::DecompressError(
            "invalid huffman code".to_string(),
        ))
    }
}

fn reverse_bits(mut val: u32, len: u8) -> u32 {
    let mut result = 0u32;
    for _ in 0..len {
        result = (result << 1) | (val & 1);
        val >>= 1;
    }
    result
}

// ── Length/distance tables ──────────────────────────────────────────────────

/// (base_length, extra_bits) for length codes 257-285
static LENGTH_TABLE: [(u16, u8); 29] = [
    (3, 0),
    (4, 0),
    (5, 0),
    (6, 0),
    (7, 0),
    (8, 0),
    (9, 0),
    (10, 0),
    (11, 1),
    (13, 1),
    (15, 1),
    (17, 1),
    (19, 2),
    (23, 2),
    (27, 2),
    (31, 2),
    (35, 3),
    (43, 3),
    (51, 3),
    (59, 3),
    (67, 4),
    (83, 4),
    (99, 4),
    (115, 4),
    (131, 5),
    (163, 5),
    (195, 5),
    (227, 5),
    (258, 0),
];

/// (base_distance, extra_bits) for distance codes 0-29
static DISTANCE_TABLE: [(u16, u8); 30] = [
    (1, 0),
    (2, 0),
    (3, 0),
    (4, 0),
    (5, 1),
    (7, 1),
    (9, 2),
    (13, 2),
    (17, 3),
    (25, 3),
    (33, 4),
    (49, 4),
    (65, 5),
    (97, 5),
    (129, 6),
    (193, 6),
    (257, 7),
    (385, 7),
    (513, 8),
    (769, 8),
    (1025, 9),
    (1537, 9),
    (2049, 10),
    (3073, 10),
    (4097, 11),
    (6145, 11),
    (8193, 12),
    (12289, 12),
    (16385, 13),
    (24577, 13),
];

/// Order of code lengths for the code-length alphabet (RFC 1951 §3.2.7)
static CL_ORDER: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

// ── Fixed Huffman tables ────────────────────────────────────────────────────

fn build_fixed_lit_len() -> HuffmanTable {
    let mut lengths = [0u8; 288];
    for i in 0..=143 {
        lengths[i] = 8;
    }
    for i in 144..=255 {
        lengths[i] = 9;
    }
    for i in 256..=279 {
        lengths[i] = 7;
    }
    for i in 280..=287 {
        lengths[i] = 8;
    }
    HuffmanTable::build(&lengths).expect("fixed lit/len table is valid")
}

fn build_fixed_dist() -> HuffmanTable {
    let lengths = [5u8; 32];
    HuffmanTable::build(&lengths).expect("fixed dist table is valid")
}

// ── Core inflate ────────────────────────────────────────────────────────────

fn inflate_stream(reader: &mut BitReader) -> Result<Vec<u8>, GitError> {
    let mut output = Vec::with_capacity(4096);

    loop {
        let bfinal = reader.read_bits(1);
        let btype = reader.read_bits(2);

        match btype {
            0 => {
                // Stored block
                reader.align_to_byte();
                let len = reader.read_u16_le();
                let nlen = reader.read_u16_le();
                if len != !nlen {
                    return Err(GitError::DecompressError(format!(
                        "stored block len/nlen mismatch: {len} vs {nlen}"
                    )));
                }
                for _ in 0..len {
                    output.push(reader.read_byte());
                }
            }
            1 => {
                // Fixed Huffman
                let lit_table = build_fixed_lit_len();
                let dist_table = build_fixed_dist();
                decode_block(reader, &lit_table, &dist_table, &mut output)?;
            }
            2 => {
                // Dynamic Huffman
                let (lit_table, dist_table) = decode_dynamic_tables(reader)?;
                decode_block(reader, &lit_table, &dist_table, &mut output)?;
            }
            _ => {
                return Err(GitError::DecompressError(format!(
                    "invalid block type: {btype}"
                )));
            }
        }

        if bfinal != 0 {
            break;
        }
    }

    Ok(output)
}

fn decode_dynamic_tables(
    reader: &mut BitReader,
) -> Result<(HuffmanTable, HuffmanTable), GitError> {
    let hlit = reader.read_bits(5) as usize + 257;
    let hdist = reader.read_bits(5) as usize + 1;
    let hclen = reader.read_bits(4) as usize + 4;

    // Read code-length code lengths in scrambled order
    let mut cl_lengths = [0u8; 19];
    for i in 0..hclen {
        cl_lengths[CL_ORDER[i]] = reader.read_bits(3) as u8;
    }

    let cl_table = HuffmanTable::build(&cl_lengths)?;

    // Decode literal/length and distance code lengths
    let total = hlit + hdist;
    let mut lengths = vec![0u8; total];
    let mut i = 0;

    while i < total {
        let sym = cl_table.decode(reader)? as usize;
        match sym {
            0..=15 => {
                lengths[i] = sym as u8;
                i += 1;
            }
            16 => {
                // Repeat previous 3-6 times
                let repeat = reader.read_bits(2) as usize + 3;
                if i == 0 {
                    return Err(GitError::DecompressError(
                        "repeat code 16 at start".to_string(),
                    ));
                }
                let prev = lengths[i - 1];
                for _ in 0..repeat {
                    if i >= total {
                        return Err(GitError::DecompressError(
                            "code length overflow".to_string(),
                        ));
                    }
                    lengths[i] = prev;
                    i += 1;
                }
            }
            17 => {
                // Repeat 0 for 3-10 times
                let repeat = reader.read_bits(3) as usize + 3;
                i += repeat;
            }
            18 => {
                // Repeat 0 for 11-138 times
                let repeat = reader.read_bits(7) as usize + 11;
                i += repeat;
            }
            _ => {
                return Err(GitError::DecompressError(format!(
                    "invalid code length symbol: {sym}"
                )));
            }
        }
    }

    let lit_table = HuffmanTable::build(&lengths[..hlit])?;
    let dist_table = HuffmanTable::build(&lengths[hlit..])?;

    Ok((lit_table, dist_table))
}

fn decode_block(
    reader: &mut BitReader,
    lit_table: &HuffmanTable,
    dist_table: &HuffmanTable,
    output: &mut Vec<u8>,
) -> Result<(), GitError> {
    loop {
        let sym = lit_table.decode(reader)?;

        match sym {
            0..=255 => {
                output.push(sym as u8);
            }
            256 => {
                // End of block
                return Ok(());
            }
            257..=285 => {
                // Length code
                let idx = (sym - 257) as usize;
                let (base_len, extra) = LENGTH_TABLE[idx];
                let length = base_len as usize + reader.read_bits(extra) as usize;

                // Distance code
                let dist_sym = dist_table.decode(reader)? as usize;
                if dist_sym >= 30 {
                    return Err(GitError::DecompressError(format!(
                        "invalid distance code: {dist_sym}"
                    )));
                }
                let (base_dist, extra) = DISTANCE_TABLE[dist_sym];
                let distance = base_dist as usize + reader.read_bits(extra) as usize;

                if distance > output.len() {
                    return Err(GitError::DecompressError(format!(
                        "distance {distance} exceeds output length {}",
                        output.len()
                    )));
                }

                // Copy from output buffer — handle overlapping copies byte-by-byte
                let start = output.len() - distance;
                for i in 0..length {
                    let byte = output[start + i];
                    output.push(byte);
                }
            }
            _ => {
                return Err(GitError::DecompressError(format!(
                    "invalid literal/length symbol: {sym}"
                )));
            }
        }
    }
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Decompress zlib-framed data (CMF + FLG header, deflate stream, Adler-32 trailer).
pub fn inflate_zlib(data: &[u8]) -> Result<Vec<u8>, GitError> {
    if data.len() < 6 {
        return Err(GitError::DecompressError("zlib data too short".to_string()));
    }

    let cmf = data[0];
    let method = cmf & 0x0F;
    if method != 8 {
        return Err(GitError::DecompressError(format!(
            "unsupported zlib method: {method}"
        )));
    }

    // Skip CMF + FLG (2 bytes), inflate raw deflate, skip trailing 4-byte Adler-32
    let mut reader = BitReader::new(&data[2..]);
    inflate_stream(&mut reader)
}

/// Decompress raw DEFLATE stream (no zlib header/trailer).
pub fn inflate_raw(data: &[u8]) -> Result<(Vec<u8>, usize), GitError> {
    let mut reader = BitReader::new(data);
    let result = inflate_stream(&mut reader)?;
    let consumed = reader.bytes_consumed();
    Ok((result, consumed))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stored_block() {
        // Zlib-compressed "hello" (stored block, no compression)
        let compressed = [
            0x78, 0x01, 0x01, 0x05, 0x00, 0xfa, 0xff, 0x68, 0x65, 0x6c, 0x6c, 0x6f, 0x06, 0x2c,
            0x02, 0x15,
        ];
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(&result, b"hello");
    }

    #[test]
    fn test_fixed_huffman() {
        // Zlib-compressed "hello" with default compression
        let compressed = [
            0x78, 0x9c, 0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x07, 0x00, 0x06, 0x2c, 0x02, 0x15,
        ];
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(&result, b"hello");
    }

    #[test]
    fn test_empty() {
        // Zlib-compressed empty bytes
        let compressed = [0x78, 0x9c, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01];
        let result = inflate_zlib(&compressed).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_repeated_data() {
        // Data with LZ77 back-references (length > distance overlap case)
        let original = b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let compressed = compress_with_python(original);
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(&result, original);
    }

    #[test]
    fn test_real_git_object() {
        // Read a real loose object from this repo's .git/ and decompress it
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let objects_dir = git_dir.join("objects");
        for entry in std::fs::read_dir(&objects_dir).unwrap() {
            let entry = entry.unwrap();
            let name = entry.file_name().to_string_lossy().to_string();
            if name.len() == 2 && name != "pa" && name != "in" {
                let subdir = objects_dir.join(&name);
                if let Ok(files) = std::fs::read_dir(&subdir) {
                    for file in files {
                        let file = file.unwrap();
                        let data = std::fs::read(file.path()).unwrap();
                        let decompressed = inflate_zlib(&data).unwrap();
                        // Must start with "blob ", "tree ", "commit ", or "tag "
                        let header = std::str::from_utf8(&decompressed[..6]).unwrap_or("");
                        assert!(
                            header.starts_with("blob ")
                                || header.starts_with("tree ")
                                || header.starts_with("commit")
                                || header.starts_with("tag "),
                            "unexpected object header: {header:?}"
                        );
                        return; // one object is enough
                    }
                }
            }
        }
        panic!("no loose objects found in repo");
    }

    #[test]
    fn test_fuzz_random_data() {
        for size in [0, 1, 10, 100, 1000, 10000, 50000] {
            let data: Vec<u8> = (0..size).map(|i| (i % 256) as u8).collect();
            let compressed = compress_with_python(&data);
            let result = inflate_zlib(&compressed).unwrap();
            assert_eq!(result, data, "mismatch at size {size}");
        }
    }

    /// Compress data with Python zlib (test helper).
    fn compress_with_python(data: &[u8]) -> Vec<u8> {
        use std::process::Command;
        let hex: String = data.iter().map(|b| format!("{b:02x}")).collect();
        let output = Command::new("python3")
            .args([
                "-c",
                &format!(
                    "import zlib,sys; sys.stdout.buffer.write(zlib.compress(bytes.fromhex('{}')))",
                    hex
                ),
            ])
            .output()
            .expect("python3 required for deflate tests");
        assert!(output.status.success(), "python3 zlib compress failed");
        output.stdout
    }
}
