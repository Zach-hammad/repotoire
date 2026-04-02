use super::error::GitError;
use super::oid::Oid;

#[derive(Debug)]
pub struct RawCommit {
    pub tree_oid: Oid,
    pub parents: Vec<Oid>,
    pub author_name: String,
    pub author_email: String,
    pub author_time: i64,
    pub committer_name: String,
    pub committer_email: String,
    pub committer_time: i64,
    pub message: String,
}

impl RawCommit {
    pub fn parse(data: &[u8]) -> Result<Self, GitError> {
        let text = std::str::from_utf8(data).map_err(|_| GitError::CorruptObject {
            path: String::new(),
            detail: "non-UTF8 commit".into(),
        })?;

        // Split headers from body at first blank line
        let (headers_str, body) = match text.find("\n\n") {
            Some(pos) => (&text[..pos], text[pos + 2..].trim_end()),
            None => (text, ""),
        };

        let mut tree_oid = None;
        let mut parents = Vec::new();
        let mut author_name = String::new();
        let mut author_email = String::new();
        let mut author_time: i64 = 0;
        let mut committer_name = String::new();
        let mut committer_email = String::new();
        let mut committer_time: i64 = 0;

        let mut lines = headers_str.lines().peekable();
        while let Some(line) = lines.next() {
            if let Some(val) = line.strip_prefix("tree ") {
                tree_oid = Some(Oid::from_hex(val.trim())?);
            } else if let Some(val) = line.strip_prefix("parent ") {
                parents.push(Oid::from_hex(val.trim())?);
            } else if let Some(val) = line.strip_prefix("author ") {
                let (name, email, time) = parse_signature(val)?;
                author_name = name;
                author_email = email;
                author_time = time;
            } else if let Some(val) = line.strip_prefix("committer ") {
                let (name, email, time) = parse_signature(val)?;
                committer_name = name;
                committer_email = email;
                committer_time = time;
            } else if line.starts_with("gpgsig ") || line.starts_with("mergetag ") {
                // Skip multi-line header: continuation lines start with space
                while lines.peek().is_some_and(|l| l.starts_with(' ')) {
                    lines.next();
                }
            }
        }

        let tree_oid = tree_oid.ok_or_else(|| GitError::CorruptObject {
            path: String::new(),
            detail: "commit missing tree".into(),
        })?;

        // Message is the first line of body (subject line)
        let message = body
            .lines()
            .next()
            .unwrap_or("")
            .to_string();

        Ok(Self {
            tree_oid,
            parents,
            author_name,
            author_email,
            author_time,
            committer_name,
            committer_email,
            committer_time,
            message,
        })
    }
}

/// Parse "Name <email> timestamp tz" signature format.
fn parse_signature(s: &str) -> Result<(String, String, i64), GitError> {
    let lt = s.find('<').ok_or_else(|| GitError::CorruptObject {
        path: String::new(),
        detail: "missing < in signature".into(),
    })?;
    let gt = s.find('>').ok_or_else(|| GitError::CorruptObject {
        path: String::new(),
        detail: "missing > in signature".into(),
    })?;

    let name = s[..lt].trim().to_string();
    let email = s[lt + 1..gt].to_string();

    // After "> " comes "timestamp tz"
    let after = s[gt + 1..].trim();
    let time: i64 = after
        .split_whitespace()
        .next()
        .unwrap_or("0")
        .parse()
        .unwrap_or(0);

    Ok((name, email, time))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_simple_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
parent b94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
author Test User <test@example.com> 1700000000 +0000\n\
committer Test User <test@example.com> 1700000000 +0000\n\
\n\
Initial commit\n\
\n\
Some details";
        let commit = RawCommit::parse(data).unwrap();
        assert_eq!(
            commit.tree_oid.to_hex(),
            "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3"
        );
        assert_eq!(commit.parents.len(), 1);
        assert_eq!(commit.author_name, "Test User");
        assert_eq!(commit.author_email, "test@example.com");
        assert_eq!(commit.author_time, 1700000000);
        assert_eq!(commit.message, "Initial commit");
    }

    #[test]
    fn test_parse_root_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
author Test <t@e.com> 1700000000 +0000\n\
committer Test <t@e.com> 1700000000 +0000\n\
\n\
root";
        let commit = RawCommit::parse(data).unwrap();
        assert!(commit.parents.is_empty());
    }

    #[test]
    fn test_parse_merge_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
parent 1111111111111111111111111111111111111111\n\
parent 2222222222222222222222222222222222222222\n\
author Test <t@e.com> 1700000000 +0000\n\
committer Test <t@e.com> 1700000000 +0000\n\
\n\
merge";
        let commit = RawCommit::parse(data).unwrap();
        assert_eq!(commit.parents.len(), 2);
    }

    #[test]
    fn test_parse_gpgsig_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
author Test <t@e.com> 1700000000 +0000\n\
committer Test <t@e.com> 1700000000 +0000\n\
gpgsig -----BEGIN PGP SIGNATURE-----\n \n wsBcBAAB\n -----END PGP SIGNATURE-----\n\
\n\
signed commit";
        let commit = RawCommit::parse(data).unwrap();
        assert_eq!(commit.message, "signed commit");
    }
}
