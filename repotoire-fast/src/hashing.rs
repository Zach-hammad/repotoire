use std::fs::File;
use std::io::{BufReader, Read};
use std::path::Path;
use rayon::prelude::*;


pub fn hash_file(path: &Path) -> Option<String> {
    let file = File::open(path).ok()?;
    let mut reader = BufReader::new(file);
    let mut context = md5::Context::new();
    let mut buffer = [0u8; 8192];
    loop {
        let bytes_read = reader.read(&mut buffer).ok()?;
        if bytes_read == 0 {
            break;
        }
        context.consume(&buffer[..bytes_read]);
    }
    

    let digest = context.compute();
    Some(format!("{:x}", digest))
}

pub fn batch_hash_files(paths: Vec<String>) -> Vec<(String, String)> {
    paths
         .into_par_iter()
         .filter_map(|path| {
            let path_ref = hash_file(Path::new(&path))?;
            Some((path, path_ref))
         })
         .collect()
}