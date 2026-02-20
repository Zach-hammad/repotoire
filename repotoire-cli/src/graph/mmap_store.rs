//! Memory-mapped graph store
//!
//! Streams graph data from disk instead of holding it all in RAM.
//! Uses mmap so the OS manages paging - only actively used data is in memory.
//!
//! Memory model:
//! - Index (in RAM): qualified_name -> file offset (~40 bytes per node)
//! - Data (mmap'd): actual node/edge data, paged by OS as needed
//!
//! For 75k files with 300k functions:
//! - Index: ~15MB in RAM
//! - Data: ~200MB on disk, OS pages in ~10-50MB at a time

use crate::graph::{CodeEdge, CodeNode, EdgeKind, GraphQuery, NodeKind};
use anyhow::{Context, Result};
use memmap2::{MmapMut, MmapOptions};
use std::collections::HashMap;
use std::fs::{File, OpenOptions};

use std::path::{Path, PathBuf};

/// Fixed-size header for the mmap file
const HEADER_SIZE: usize = 64;
const MAGIC: &[u8; 8] = b"REPOMMAP";
const VERSION: u32 = 1;

/// On-disk node format (fixed size for easy indexing)
#[repr(C, packed)]
#[derive(Clone, Copy, Debug)]
struct DiskNode {
    kind: u8,           // NodeKind as u8
    name_offset: u32,   // Offset into string table
    name_len: u16,
    qn_offset: u32,     // Qualified name offset
    qn_len: u16,
    file_offset: u32,   // File path offset
    file_len: u16,
    line_start: u32,
    line_end: u32,
    // Properties packed as flags + values
    flags: u16,         // is_async, etc.
    complexity: u16,
    method_count: u16,
    _padding: [u8; 2],
}

const DISK_NODE_SIZE: usize = std::mem::size_of::<DiskNode>();

/// On-disk edge format
#[repr(C, packed)]
#[derive(Clone, Copy, Debug)]
struct DiskEdge {
    kind: u8,           // EdgeKind as u8
    source_idx: u32,    // Index into node array
    target_idx: u32,    // Index into node array
    flags: u8,          // is_type_only, etc.
    _padding: [u8; 2],
}

const DISK_EDGE_SIZE: usize = std::mem::size_of::<DiskEdge>();

/// Memory-mapped graph store
pub struct MmapGraphStore {
    /// Path to the mmap file
    path: PathBuf,
    
    /// Memory-mapped data (nodes + edges + strings)
    mmap: Option<MmapMut>,
    
    /// In-memory index: qualified_name -> node index
    qn_to_idx: HashMap<String, u32>,
    
    /// In-memory index: file_path -> list of function/class indices
    file_to_nodes: HashMap<String, Vec<u32>>,
    
    /// Number of nodes
    node_count: u32,
    
    /// Number of edges
    edge_count: u32,
    
    /// Offset where edges start in the mmap
    edges_offset: usize,
    
    /// Offset where string table starts
    strings_offset: usize,
    
    /// Builder state (used during construction)
    builder: Option<MmapBuilder>,
}

/// Builder for constructing the mmap file
struct MmapBuilder {
    nodes: Vec<DiskNode>,
    edges: Vec<DiskEdge>,
    strings: Vec<u8>,
    string_offsets: HashMap<String, u32>,
    qn_to_idx: HashMap<String, u32>,
    file_to_nodes: HashMap<String, Vec<u32>>,
}

impl MmapBuilder {
    fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            strings: Vec::new(),
            string_offsets: HashMap::new(),
            qn_to_idx: HashMap::new(),
            file_to_nodes: HashMap::new(),
        }
    }
    
    /// Intern a string, returning its offset
    fn intern(&mut self, s: &str) -> (u32, u16) {
        if let Some(&offset) = self.string_offsets.get(s) {
            return (offset, s.len() as u16);
        }
        
        let offset = self.strings.len() as u32;
        self.strings.extend_from_slice(s.as_bytes());
        self.string_offsets.insert(s.to_string(), offset);
        (offset, s.len() as u16)
    }
    
    fn add_node(&mut self, node: &CodeNode) -> u32 {
        let idx = self.nodes.len() as u32;
        
        let (name_offset, name_len) = self.intern(&node.name);
        let (qn_offset, qn_len) = self.intern(&node.qualified_name);
        let (file_offset, file_len) = self.intern(&node.file_path);
        
        let kind = match node.kind {
            NodeKind::File => 0,
            NodeKind::Function => 1,
            NodeKind::Class => 2,
            NodeKind::Module => 3,
            NodeKind::Variable => 4,
            NodeKind::Commit => 5,
        };
        
        let is_async = node.properties.get("is_async")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let complexity = node.get_i64("complexity").unwrap_or(1) as u16;
        let method_count = node.get_i64("methodCount").unwrap_or(0) as u16;
        
        let flags = if is_async { 1 } else { 0 };
        
        self.nodes.push(DiskNode {
            kind,
            name_offset,
            name_len,
            qn_offset,
            qn_len,
            file_offset,
            file_len,
            line_start: node.line_start,
            line_end: node.line_end,
            flags,
            complexity,
            method_count,
            _padding: [0; 2],
        });
        
        // Update indices
        self.qn_to_idx.insert(node.qualified_name.clone(), idx);
        
        if matches!(node.kind, NodeKind::Function | NodeKind::Class) {
            self.file_to_nodes
                .entry(node.file_path.clone())
                .or_default()
                .push(idx);
        }
        
        idx
    }
    
    fn add_edge(&mut self, source_qn: &str, target_qn: &str, edge: &CodeEdge) {
        let source_idx = match self.qn_to_idx.get(source_qn) {
            Some(&idx) => idx,
            None => return,
        };
        let target_idx = match self.qn_to_idx.get(target_qn) {
            Some(&idx) => idx,
            None => return,
        };
        
        let kind = match edge.kind {
            EdgeKind::Contains => 0,
            EdgeKind::Calls => 1,
            EdgeKind::Imports => 2,
            EdgeKind::Inherits => 3,
            EdgeKind::Uses => 4,
            EdgeKind::ModifiedIn => 5,
        };
        
        let is_type_only = edge.properties.get("is_type_only")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let flags = if is_type_only { 1 } else { 0 };
        
        self.edges.push(DiskEdge {
            kind,
            source_idx,
            target_idx,
            flags,
            _padding: [0; 2],
        });
    }
    
    /// Finalize and write to disk
    fn finalize(self, path: &Path) -> Result<MmapGraphStore> {
        let node_count = self.nodes.len() as u32;
        let edge_count = self.edges.len() as u32;
        
        // Calculate offsets
        let nodes_size = self.nodes.len() * DISK_NODE_SIZE;
        let edges_size = self.edges.len() * DISK_EDGE_SIZE;
        let strings_size = self.strings.len();
        
        let edges_offset = HEADER_SIZE + nodes_size;
        let strings_offset = edges_offset + edges_size;
        let total_size = strings_offset + strings_size;
        
        // Create and write file
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(path)
            .with_context(|| format!("Failed to create mmap file: {}", path.display()))?;
        
        file.set_len(total_size as u64)?;

        // SAFETY: We just created the file and set its length to total_size.
        // The mutable mapping is the only reference to this file. The mapping
        // lifetime is tied to this function scope.
        let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };

        // Write header
        mmap[0..8].copy_from_slice(MAGIC);
        mmap[8..12].copy_from_slice(&VERSION.to_le_bytes());
        mmap[12..16].copy_from_slice(&node_count.to_le_bytes());
        mmap[16..20].copy_from_slice(&edge_count.to_le_bytes());
        mmap[20..24].copy_from_slice(&(edges_offset as u32).to_le_bytes());
        mmap[24..28].copy_from_slice(&(strings_offset as u32).to_le_bytes());

        // Write nodes
        // Validate that nodes_size matches the Vec's actual byte footprint
        debug_assert_eq!(nodes_size, self.nodes.len() * std::mem::size_of::<DiskNode>());
        // SAFETY: DiskNode is repr(C, packed) and Copy. The Vec's backing allocation is
        // contiguous and contains exactly self.nodes.len() elements. nodes_size equals
        // self.nodes.len() * size_of::<DiskNode>(), so the slice covers valid initialized memory.
        // The pointer is valid and aligned for u8 reads (u8 has alignment 1).
        let nodes_bytes = unsafe {
            std::slice::from_raw_parts(
                self.nodes.as_ptr() as *const u8,
                nodes_size,
            )
        };
        mmap[HEADER_SIZE..HEADER_SIZE + nodes_size].copy_from_slice(nodes_bytes);

        // Write edges
        // Validate that edges_size matches the Vec's actual byte footprint
        debug_assert_eq!(edges_size, self.edges.len() * std::mem::size_of::<DiskEdge>());
        // SAFETY: DiskEdge is repr(C, packed) and Copy. The Vec's backing allocation is
        // contiguous and contains exactly self.edges.len() elements. edges_size equals
        // self.edges.len() * size_of::<DiskEdge>(), so the slice covers valid initialized memory.
        // The pointer is valid and aligned for u8 reads (u8 has alignment 1).
        let edges_bytes = unsafe {
            std::slice::from_raw_parts(
                self.edges.as_ptr() as *const u8,
                edges_size,
            )
        };
        mmap[edges_offset..edges_offset + edges_size].copy_from_slice(edges_bytes);
        
        // Write strings
        mmap[strings_offset..strings_offset + strings_size].copy_from_slice(&self.strings);
        
        // Flush to disk
        mmap.flush()?;
        
        Ok(MmapGraphStore {
            path: path.to_path_buf(),
            mmap: Some(mmap),
            qn_to_idx: self.qn_to_idx,
            file_to_nodes: self.file_to_nodes,
            node_count,
            edge_count,
            edges_offset,
            strings_offset,
            builder: None,
        })
    }
}

impl MmapGraphStore {
    /// Create a new builder for constructing a mmap store
    pub fn builder() -> Self {
        Self {
            path: PathBuf::new(),
            mmap: None,
            qn_to_idx: HashMap::new(),
            file_to_nodes: HashMap::new(),
            node_count: 0,
            edge_count: 0,
            edges_offset: 0,
            strings_offset: 0,
            builder: Some(MmapBuilder::new()),
        }
    }
    
    /// Open an existing mmap store
    pub fn open(path: &Path) -> Result<Self> {
        let file = File::open(path)
            .with_context(|| format!("Failed to open mmap file: {}", path.display()))?;
        
        // SAFETY: We only create a read-only mapping of a file we successfully opened.
        // The OS manages page faults; the mapping is valid for the file's lifetime.
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        // Validate minimum file size before reading header fields
        if mmap.len() < HEADER_SIZE {
            anyhow::bail!(
                "Corrupt mmap file: size {} is smaller than header size {}",
                mmap.len(),
                HEADER_SIZE
            );
        }

        // Verify header
        if &mmap[0..8] != MAGIC {
            anyhow::bail!("Invalid mmap file: bad magic");
        }
        
        let version = u32::from_le_bytes(mmap[8..12].try_into()?);
        if version != VERSION {
            anyhow::bail!("Unsupported mmap version: {}", version);
        }
        
        let node_count = u32::from_le_bytes(mmap[12..16].try_into()?);
        let edge_count = u32::from_le_bytes(mmap[16..20].try_into()?);
        let edges_offset = u32::from_le_bytes(mmap[20..24].try_into()?) as usize;
        let strings_offset = u32::from_le_bytes(mmap[24..28].try_into()?) as usize;

        // Validate that the declared section offsets and sizes fit within the file
        let expected_nodes_end = HEADER_SIZE
            .checked_add((node_count as usize).checked_mul(DISK_NODE_SIZE).ok_or_else(|| {
                anyhow::anyhow!("Corrupt mmap file: node region size overflow")
            })?)
            .ok_or_else(|| anyhow::anyhow!("Corrupt mmap file: node region offset overflow"))?;

        if expected_nodes_end > mmap.len() {
            anyhow::bail!(
                "Corrupt mmap file: node region ends at {} but file size is {}",
                expected_nodes_end,
                mmap.len()
            );
        }
        if edges_offset > mmap.len() {
            anyhow::bail!(
                "Corrupt mmap file: edges_offset {} exceeds file size {}",
                edges_offset,
                mmap.len()
            );
        }
        let expected_edges_end = edges_offset
            .checked_add((edge_count as usize).checked_mul(DISK_EDGE_SIZE).ok_or_else(|| {
                anyhow::anyhow!("Corrupt mmap file: edge region size overflow")
            })?)
            .ok_or_else(|| anyhow::anyhow!("Corrupt mmap file: edge region offset overflow"))?;

        if expected_edges_end > mmap.len() {
            anyhow::bail!(
                "Corrupt mmap file: edge region ends at {} but file size is {}",
                expected_edges_end,
                mmap.len()
            );
        }
        if strings_offset > mmap.len() {
            anyhow::bail!(
                "Corrupt mmap file: strings_offset {} exceeds file size {}",
                strings_offset,
                mmap.len()
            );
        }

        // Build in-memory indices by scanning nodes
        let mut qn_to_idx = HashMap::with_capacity(node_count as usize);
        let mut file_to_nodes: HashMap<String, Vec<u32>> = HashMap::new();
        
        for idx in 0..node_count {
            let node_offset = HEADER_SIZE + (idx as usize) * DISK_NODE_SIZE;
            // Bounds check before unsafe read (#12)
            if node_offset + DISK_NODE_SIZE > mmap.len() {
                anyhow::bail!(
                    "Corrupt mmap store: node {} offset {} exceeds file size {}",
                    idx, node_offset, mmap.len()
                );
            }
            // SAFETY: We just verified node_offset + DISK_NODE_SIZE <= mmap.len(), so the
            // read is fully within bounds. DiskNode is repr(C, packed) and Copy, so
            // read_unaligned correctly handles the packed layout without alignment issues.
            let disk_node: DiskNode = unsafe {
                std::ptr::read_unaligned(mmap[node_offset..].as_ptr() as *const DiskNode)
            };

            // Read qualified name (with bounds check)
            let qn_start = strings_offset + disk_node.qn_offset as usize;
            let qn_end = qn_start + disk_node.qn_len as usize;
            if qn_end > mmap.len() {
                anyhow::bail!("Corrupt mmap store: string offset {} exceeds file size", qn_end);
            }
            let qn = std::str::from_utf8(&mmap[qn_start..qn_end])?.to_string();
            
            qn_to_idx.insert(qn, idx);
            
            // Index functions/classes by file
            if disk_node.kind == 1 || disk_node.kind == 2 {
                let file_start = strings_offset + disk_node.file_offset as usize;
                let file_end = file_start + disk_node.file_len as usize;
                if file_end > mmap.len() {
                    anyhow::bail!(
                        "Corrupt mmap store: file path string offset {} exceeds file size {}",
                        file_end,
                        mmap.len()
                    );
                }
                let file_path = std::str::from_utf8(&mmap[file_start..file_end])?.to_string();

                file_to_nodes.entry(file_path).or_default().push(idx);
            }
        }
        
        // Drop the read-only mmap, reopen as mutable for potential updates
        drop(mmap);
        let file = OpenOptions::new().read(true).write(true).open(path)
            .with_context(|| format!("Failed to reopen mmap file for writing: {}", path.display()))?;
        // SAFETY: The read-only mapping has been dropped. We reopen the same validated file
        // as a mutable mapping. The file was fully validated above (header, offsets, sizes).
        // No other mapping of this file exists at this point.
        let mmap = unsafe { MmapOptions::new().map_mut(&file)? };
        
        Ok(Self {
            path: path.to_path_buf(),
            mmap: Some(mmap),
            qn_to_idx,
            file_to_nodes,
            node_count,
            edge_count,
            edges_offset,
            strings_offset,
            builder: None,
        })
    }
    
    /// Add a node (builder mode only)
    pub fn add_node(&mut self, node: CodeNode) {
        if let Some(ref mut builder) = self.builder {
            builder.add_node(&node);
        }
    }
    
    /// Add an edge (builder mode only)
    pub fn add_edge(&mut self, source: &str, target: &str, edge: CodeEdge) {
        if let Some(ref mut builder) = self.builder {
            builder.add_edge(source, target, &edge);
        }
    }
    
    /// Finalize the builder and write to disk
    pub fn finalize(mut self, path: &Path) -> Result<Self> {
        if let Some(builder) = self.builder.take() {
            builder.finalize(path)
        } else {
            Ok(self)
        }
    }
    
    /// Read a node from the mmap
    fn read_node(&self, idx: u32) -> Option<CodeNode> {
        let mmap = self.mmap.as_ref()?;
        
        if idx >= self.node_count {
            return None;
        }
        
        let node_offset = HEADER_SIZE + (idx as usize) * DISK_NODE_SIZE;

        // Bounds check: ensure the full DiskNode fits within the mmap region
        if node_offset + DISK_NODE_SIZE > mmap.len() {
            return None;
        }

        // SAFETY: We verified idx < node_count and that node_offset + DISK_NODE_SIZE <= mmap.len().
        // DiskNode is repr(C, packed) and Copy, so read_unaligned is appropriate for packed structs.
        let disk_node: DiskNode = unsafe {
            std::ptr::read_unaligned(mmap[node_offset..].as_ptr() as *const DiskNode)
        };

        // Read strings
        let name = self.read_string(disk_node.name_offset, disk_node.name_len)?;
        let qn = self.read_string(disk_node.qn_offset, disk_node.qn_len)?;
        let file_path = self.read_string(disk_node.file_offset, disk_node.file_len)?;
        
        let kind = match disk_node.kind {
            0 => NodeKind::File,
            1 => NodeKind::Function,
            2 => NodeKind::Class,
            3 => NodeKind::Module,
            4 => NodeKind::Variable,
            5 => NodeKind::Commit,
            _ => return None,
        };
        
        let mut node = CodeNode {
            kind,
            name,
            qualified_name: qn,
            file_path,
            line_start: disk_node.line_start,
            line_end: disk_node.line_end,
            language: None,
            properties: HashMap::new(),
        };
        
        // Restore properties
        if disk_node.flags & 1 != 0 {
            node.properties.insert("is_async".to_string(), true.into());
        }
        if disk_node.complexity > 0 {
            node.properties.insert("complexity".to_string(), (disk_node.complexity as i64).into());
        }
        if disk_node.method_count > 0 {
            node.properties.insert("methodCount".to_string(), (disk_node.method_count as i64).into());
        }
        
        Some(node)
    }
    
    fn read_string(&self, offset: u32, len: u16) -> Option<String> {
        let mmap = self.mmap.as_ref()?;
        let start = self.strings_offset + offset as usize;
        let end = start + len as usize;
        
        if end > mmap.len() {
            return None;
        }
        
        std::str::from_utf8(&mmap[start..end]).ok().map(|s| s.to_string())
    }
    
    /// Read an edge from the mmap
    fn read_edge(&self, idx: u32) -> Option<(u32, u32, EdgeKind, bool)> {
        let mmap = self.mmap.as_ref()?;
        
        if idx >= self.edge_count {
            return None;
        }
        
        let edge_offset = self.edges_offset + (idx as usize) * DISK_EDGE_SIZE;

        // Bounds check: ensure the full DiskEdge fits within the mmap region
        if edge_offset + DISK_EDGE_SIZE > mmap.len() {
            return None;
        }

        // SAFETY: We verified idx < edge_count and that edge_offset + DISK_EDGE_SIZE <= mmap.len().
        // DiskEdge is repr(C, packed) and Copy, so read_unaligned is appropriate for packed structs.
        let disk_edge: DiskEdge = unsafe {
            std::ptr::read_unaligned(mmap[edge_offset..].as_ptr() as *const DiskEdge)
        };
        
        let kind = match disk_edge.kind {
            0 => EdgeKind::Contains,
            1 => EdgeKind::Calls,
            2 => EdgeKind::Imports,
            3 => EdgeKind::Inherits,
            4 => EdgeKind::Uses,
            5 => EdgeKind::ModifiedIn,
            _ => return None,
        };
        
        let is_type_only = disk_edge.flags & 1 != 0;
        
        Some((disk_edge.source_idx, disk_edge.target_idx, kind, is_type_only))
    }
    
    /// Memory usage estimate
    pub fn memory_usage(&self) -> MmapMemoryStats {
        let index_size = self.qn_to_idx.len() * 48 + // HashMap overhead + String + u32
                         self.file_to_nodes.len() * 64; // HashMap + String + Vec
        
        let mmap_size = self.mmap.as_ref().map(|m| m.len()).unwrap_or(0);
        
        // Estimate resident pages (OS typically keeps ~10-20% resident)
        let estimated_resident = mmap_size / 10;
        
        MmapMemoryStats {
            index_bytes: index_size,
            mmap_bytes: mmap_size,
            estimated_resident,
        }
    }
}

impl GraphQuery for MmapGraphStore {
    fn get_functions(&self) -> Vec<CodeNode> {
        (0..self.node_count)
            .filter_map(|idx| {
                let node = self.read_node(idx)?;
                if matches!(node.kind, NodeKind::Function) {
                    Some(node)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn get_classes(&self) -> Vec<CodeNode> {
        (0..self.node_count)
            .filter_map(|idx| {
                let node = self.read_node(idx)?;
                if matches!(node.kind, NodeKind::Class) {
                    Some(node)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn get_files(&self) -> Vec<CodeNode> {
        (0..self.node_count)
            .filter_map(|idx| {
                let node = self.read_node(idx)?;
                if matches!(node.kind, NodeKind::File) {
                    Some(node)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.file_to_nodes
            .get(file_path)
            .map(|indices| {
                indices.iter()
                    .filter_map(|&idx| {
                        let node = self.read_node(idx)?;
                        if matches!(node.kind, NodeKind::Function) {
                            Some(node)
                        } else {
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    }
    
    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.file_to_nodes
            .get(file_path)
            .map(|indices| {
                indices.iter()
                    .filter_map(|&idx| {
                        let node = self.read_node(idx)?;
                        if matches!(node.kind, NodeKind::Class) {
                            Some(node)
                        } else {
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    }
    
    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        let idx = *self.qn_to_idx.get(qn)?;
        self.read_node(idx)
    }
    
    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let target_idx = match self.qn_to_idx.get(qn) {
            Some(&idx) => idx,
            None => return Vec::new(),
        };
        
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if target == target_idx && matches!(kind, EdgeKind::Calls) {
                    self.read_node(source)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let source_idx = match self.qn_to_idx.get(qn) {
            Some(&idx) => idx,
            None => return Vec::new(),
        };
        
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if source == source_idx && matches!(kind, EdgeKind::Calls) {
                    self.read_node(target)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn call_fan_in(&self, qn: &str) -> usize {
        let target_idx = match self.qn_to_idx.get(qn) {
            Some(&idx) => idx,
            None => return 0,
        };
        
        (0..self.edge_count)
            .filter(|&idx| {
                self.read_edge(idx)
                    .map(|(_, target, kind, _)| target == target_idx && matches!(kind, EdgeKind::Calls))
                    .unwrap_or(false)
            })
            .count()
    }
    
    fn call_fan_out(&self, qn: &str) -> usize {
        let source_idx = match self.qn_to_idx.get(qn) {
            Some(&idx) => idx,
            None => return 0,
        };
        
        (0..self.edge_count)
            .filter(|&idx| {
                self.read_edge(idx)
                    .map(|(source, _, kind, _)| source == source_idx && matches!(kind, EdgeKind::Calls))
                    .unwrap_or(false)
            })
            .count()
    }
    
    fn get_calls(&self) -> Vec<(String, String)> {
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if !matches!(kind, EdgeKind::Calls) {
                    return None;
                }
                let source_node = self.read_node(source)?;
                let target_node = self.read_node(target)?;
                Some((source_node.qualified_name, target_node.qualified_name))
            })
            .collect()
    }
    
    fn get_imports(&self) -> Vec<(String, String)> {
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if !matches!(kind, EdgeKind::Imports) {
                    return None;
                }
                let source_node = self.read_node(source)?;
                let target_node = self.read_node(target)?;
                Some((source_node.qualified_name, target_node.qualified_name))
            })
            .collect()
    }
    
    fn get_inheritance(&self) -> Vec<(String, String)> {
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if !matches!(kind, EdgeKind::Inherits) {
                    return None;
                }
                let source_node = self.read_node(source)?;
                let target_node = self.read_node(target)?;
                Some((source_node.qualified_name, target_node.qualified_name))
            })
            .collect()
    }
    
    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let parent_idx = match self.qn_to_idx.get(qn) {
            Some(&idx) => idx,
            None => return Vec::new(),
        };
        
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if target == parent_idx && matches!(kind, EdgeKind::Inherits) {
                    self.read_node(source)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let target_idx = match self.qn_to_idx.get(qn) {
            Some(&idx) => idx,
            None => return Vec::new(),
        };
        
        (0..self.edge_count)
            .filter_map(|idx| {
                let (source, target, kind, _) = self.read_edge(idx)?;
                if target == target_idx && matches!(kind, EdgeKind::Imports) {
                    self.read_node(source)
                } else {
                    None
                }
            })
            .collect()
    }
    
    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        // For mmap mode, skip cycle detection (expensive full scan)
        // Return empty - caller should use simpler heuristics
        Vec::new()
    }
    
    fn stats(&self) -> HashMap<String, i64> {
        let mut stats = HashMap::new();
        
        let mut files = 0i64;
        let mut functions = 0i64;
        let mut classes = 0i64;
        
        for idx in 0..self.node_count {
            if let Some(node) = self.read_node(idx) {
                match node.kind {
                    NodeKind::File => files += 1,
                    NodeKind::Function => functions += 1,
                    NodeKind::Class => classes += 1,
                    _ => {}
                }
            }
        }
        
        let mut calls = 0i64;
        let mut imports = 0i64;
        
        for idx in 0..self.edge_count {
            if let Some((_, _, kind, _)) = self.read_edge(idx) {
                match kind {
                    EdgeKind::Calls => calls += 1,
                    EdgeKind::Imports => imports += 1,
                    _ => {}
                }
            }
        }
        
        stats.insert("files".to_string(), files);
        stats.insert("functions".to_string(), functions);
        stats.insert("classes".to_string(), classes);
        stats.insert("calls".to_string(), calls);
        stats.insert("imports".to_string(), imports);
        
        stats
    }
}

/// Memory statistics for mmap store
#[derive(Debug, Clone)]
pub struct MmapMemoryStats {
    /// Bytes used by in-memory indices
    pub index_bytes: usize,
    /// Total bytes in the mmap file
    pub mmap_bytes: usize,
    /// Estimated resident memory (OS-managed)
    pub estimated_resident: usize,
}

impl MmapMemoryStats {
    pub fn human_readable(&self) -> String {
        format!(
            "Index: {:.1}MB, Disk: {:.1}MB, Est. Resident: {:.1}MB",
            self.index_bytes as f64 / 1024.0 / 1024.0,
            self.mmap_bytes as f64 / 1024.0 / 1024.0,
            self.estimated_resident as f64 / 1024.0 / 1024.0,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    
    #[test]
    fn test_mmap_basic() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.mmap");
        
        // Build
        let mut store = MmapGraphStore::builder();
        
        store.add_node(CodeNode::file("src/main.rs").with_qualified_name("src/main.rs"));
        store.add_node(
            CodeNode::function("main", "src/main.rs")
                .with_qualified_name("src/main.rs::main")
                .with_lines(1, 10)
                .with_property("complexity", 5)
        );
        store.add_node(
            CodeNode::function("helper", "src/main.rs")
                .with_qualified_name("src/main.rs::helper")
                .with_lines(12, 20)
        );
        
        store.add_edge("src/main.rs::main", "src/main.rs::helper", CodeEdge::calls());
        
        // Finalize
        let store = store.finalize(&path).unwrap();
        
        // Query
        assert_eq!(store.get_functions().len(), 2);
        assert_eq!(store.get_files().len(), 1);
        
        let main = store.get_node("src/main.rs::main").unwrap();
        assert_eq!(main.name, "main");
        assert_eq!(main.get_i64("complexity"), Some(5));
        
        let callees = store.get_callees("src/main.rs::main");
        assert_eq!(callees.len(), 1);
        assert_eq!(callees[0].name, "helper");
        
        // Reopen
        drop(store);
        let store = MmapGraphStore::open(&path).unwrap();
        assert_eq!(store.get_functions().len(), 2);
    }
}
