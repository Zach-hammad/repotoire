//! Performance detectors — N+1 queries, sync-in-async, hot loops.

mod n_plus_one;
mod regex_in_loop;
mod sync_in_async;

pub use n_plus_one::NPlusOneDetector;
pub use regex_in_loop::RegexInLoopDetector;
pub use sync_in_async::SyncInAsyncDetector;
