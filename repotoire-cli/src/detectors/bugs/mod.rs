//! Bug pattern detectors — runtime errors, logic errors, missing async handling.

mod broad_exception;
mod callback_hell;
mod empty_catch;
mod generator_misuse;
mod global_variables;
mod implicit_coercion;
mod infinite_loop;
mod missing_await;
mod mutable_default_args;
mod string_concat_loop;
mod unhandled_promise;
mod unreachable_code;
mod wildcard_imports;

pub use broad_exception::BroadExceptionDetector;
pub use callback_hell::CallbackHellDetector;
pub use empty_catch::EmptyCatchDetector;
pub use generator_misuse::GeneratorMisuseDetector;
pub use global_variables::GlobalVariablesDetector;
pub use implicit_coercion::ImplicitCoercionDetector;
pub use infinite_loop::InfiniteLoopDetector;
pub use missing_await::MissingAwaitDetector;
pub use mutable_default_args::MutableDefaultArgsDetector;
pub use string_concat_loop::StringConcatLoopDetector;
pub use unhandled_promise::UnhandledPromiseDetector;
pub use unreachable_code::UnreachableCodeDetector;
pub use wildcard_imports::WildcardImportsDetector;
