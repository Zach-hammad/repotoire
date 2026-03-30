//! SIMD-accelerated string searching via `memchr::memmem`.
//!
//! Pre-built `Finder` instances for patterns used in hot detector loops.
//! Each `Finder` precomputes a SIMD-friendly search state once (via `LazyLock`),
//! then reuses it for every line/file check -- significantly faster than
//! `str::contains()` for short needles.

use memchr::memmem::Finder;
use std::sync::LazyLock;

/// Declare a `LazyLock<Finder>` static for SIMD-accelerated substring search.
macro_rules! finder {
    ($name:ident, $needle:literal) => {
        pub static $name: LazyLock<Finder<'static>> =
            LazyLock::new(|| Finder::new($needle));
    };
}

/// Check if a `Finder` matches inside a `&str`.
#[inline(always)]
pub fn find_in(finder: &Finder<'_>, haystack: &str) -> bool {
    finder.find(haystack.as_bytes()).is_some()
}

// ── User input indicators ────────────────────────────────────────────────
// Used by command_injection, ssrf, path_traversal, xss, log_injection

finder!(FIND_REQ_DOT, b"req.");
finder!(FIND_REQUEST_DOT, b"request.");
finder!(FIND_REQ_BODY, b"req.body");
finder!(FIND_REQ_QUERY, b"req.query");
finder!(FIND_REQ_PARAMS, b"req.params");
finder!(FIND_REQ_FILE, b"req.file");
finder!(FIND_REQUEST_BODY, b"request.body");
finder!(FIND_REQUEST_QUERY, b"request.query");
finder!(FIND_REQUEST_PARAMS, b"request.params");
finder!(FIND_PARAMS_BRACKET, b"params[");
finder!(FIND_QUERY_BRACKET, b"query[");
finder!(FIND_BODY_BRACKET, b"body[");
finder!(FIND_BODY_GET, b"body.get");
finder!(FIND_QUERY_GET, b"query.get");
finder!(FIND_CTX_PARAMS, b"ctx.params");
finder!(FIND_CTX_QUERY, b"ctx.query");
finder!(FIND_PROPS_DOT, b"props.");
finder!(FIND_INPUT, b"input");
finder!(FIND_ARGV, b"argv");
finder!(FIND_ARGS, b"args");
finder!(FIND_USER, b"user");
finder!(FIND_PARAMS, b"params");

// ── Template / interpolation indicators ──────────────────────────────────

finder!(FIND_F_QUOTE, b"f\"");
finder!(FIND_DOLLAR_BRACE, b"${");
finder!(FIND_PLUS_SPACE, b"+ ");
finder!(FIND_DOT_FORMAT, b".format(");
finder!(FIND_BACKTICK, b"`");

// ── Command injection pre-filter patterns ────────────────────────────────

finder!(FIND_OS_SYSTEM, b"os.system");
finder!(FIND_OS_POPEN, b"os.popen");
finder!(FIND_SUBPROCESS, b"subprocess");
finder!(FIND_CHILD_PROCESS, b"child_process");
finder!(FIND_EXEC_SYNC, b"execSync");
finder!(FIND_EXEC_ASYNC, b"execAsync");
finder!(FIND_SPAWN_SYNC, b"spawnSync");
finder!(FIND_SHELL_EXEC, b"shell_exec");
finder!(FIND_PROC_OPEN, b"proc_open");
finder!(FIND_EXEC_COMMAND, b"exec.Command");
finder!(FIND_RUNTIME_GETRUNTIME, b"Runtime.getRuntime");
finder!(FIND_PROCESS_BUILDER, b"ProcessBuilder");
finder!(FIND_SHELL_TRUE, b"shell=True");
finder!(FIND_SHELL_TRUE_JS, b"shell: true");
finder!(FIND_EXEC_PAREN, b"exec(");
finder!(FIND_EXEC_SYNC_PAREN, b"execSync(");
finder!(FIND_EXEC_ASYNC_PAREN, b"execAsync(");

// ── Eval detector pre-filter patterns ────────────────────────────────────

finder!(FIND_EVAL_PAREN, b"eval(");
finder!(FIND_DUNDER_IMPORT, b"__import__");
finder!(FIND_IMPORT_MODULE, b"import_module");

// ── Build script / path indicators ───────────────────────────────────────

finder!(FIND_SCRIPTS, b"/scripts/");
finder!(FIND_BUILD, b"/build/");
finder!(FIND_TOOLS, b"/tools/");
finder!(FIND_CI, b"/ci/");
finder!(FIND_GITHUB, b"/.github/");
finder!(FIND_GULP, b"/gulp");
finder!(FIND_GRUNT, b"/grunt");
finder!(FIND_WEBPACK, b"webpack");
finder!(FIND_ROLLUP, b"rollup");
finder!(FIND_VITE_CONFIG, b"vite.config");

// ── Safe source patterns ─────────────────────────────────────────────────

finder!(FIND_PROCESS_ENV, b"process.env");
finder!(FIND_DIRNAME, b"__dirname");
finder!(FIND_FILENAME, b"__filename");
finder!(FIND_PATH_JOIN, b"path.join");
finder!(FIND_PATH_RESOLVE, b"path.resolve");
finder!(FIND_CWD, b"cwd()");

// ── XSS dynamic input patterns ───────────────────────────────────────────

finder!(FIND_FETCH_PAREN, b"fetch(");
finder!(FIND_XMLHTTPREQUEST, b"XMLHttpRequest");
finder!(FIND_LOCATION_SEARCH, b"location.search");
finder!(FIND_LOCATION_HASH, b"location.hash");
finder!(FIND_DOCUMENT_COOKIE, b"document.cookie");
finder!(FIND_WINDOW_NAME, b"window.name");
finder!(FIND_POSTMESSAGE, b"postMessage");

// ── SSRF safe patterns ───────────────────────────────────────────────────

finder!(FIND_API_URL, b"API_URL");
finder!(FIND_BASE_URL, b"BASE_URL");
finder!(FIND_SERVER_URL, b"SERVER_URL");
finder!(FIND_BACKEND_URL, b"BACKEND_URL");
finder!(FIND_API_URL_CAMEL, b"apiUrl");
finder!(FIND_BASE_URL_CAMEL, b"baseUrl");
finder!(FIND_FETCH_SLASH_SINGLE, b"fetch('/");
finder!(FIND_FETCH_BACKTICK_SLASH, b"fetch(`/");
finder!(FIND_FETCH_DQUOTE_SLASH, b"fetch(\"/");

// ── Path traversal patterns ──────────────────────────────────────────────

finder!(FIND_OPEN_PAREN, b"open(");
finder!(FIND_READ_FILE, b"readFile");
finder!(FIND_WRITE_FILE, b"writeFile");
finder!(FIND_OS_PATH, b"os.path");
finder!(FIND_SEND_FILE, b"sendFile");
finder!(FIND_SEND_FILE_SNAKE, b"send_file");
finder!(FIND_SERVE_FILE, b"serve_file");
finder!(FIND_UNLINK, b"unlink");
finder!(FIND_RMDIR, b"rmdir");
finder!(FIND_MKDIR, b"mkdir");
finder!(FIND_COPY_FILE, b"copyFile");
finder!(FIND_RENAME_PAREN, b"rename(");
finder!(FIND_OS_REMOVE, b"os.remove");
finder!(FIND_SHUTIL, b"shutil");
finder!(FIND_FILEPATH, b"filepath");
finder!(FIND_PATHLIB, b"pathlib");
finder!(FIND_CREATE_READ_STREAM, b"createReadStream");
finder!(FIND_CREATE_WRITE_STREAM, b"createWriteStream");
finder!(FIND_APPEND_FILE, b"appendFile");
finder!(FIND_STAT_SYNC, b"statSync");
finder!(FIND_ACCESS_SYNC, b"accessSync");

// ── Path traversal user input (specific) ─────────────────────────────────

finder!(FIND_REQUEST_GET, b"request.GET");
finder!(FIND_REQUEST_POST, b"request.POST");
finder!(FIND_REQUEST_FILES, b"request.FILES");
finder!(FIND_REQUEST_ARGS, b"request.args");
finder!(FIND_REQUEST_FORM, b"request.form");
finder!(FIND_REQUEST_DATA, b"request.data");
finder!(FIND_REQUEST_VALUES, b"request.values");
finder!(FIND_INPUT_PAREN, b"input(");
finder!(FIND_SYS_ARGV, b"sys.argv");
finder!(FIND_PROCESS_ARGV, b"process.argv");
finder!(FIND_R_URL, b"r.URL");
finder!(FIND_C_PARAM, b"c.Param");
finder!(FIND_C_QUERY, b"c.Query");
finder!(FIND_FORM_VALUE, b"FormValue");
finder!(FIND_R_FORM, b"r.Form");

// ── Log injection patterns ───────────────────────────────────────────────

finder!(FIND_REQUEST, b"request");

// ── User input helper patterns ───────────────────────────────────────────

finder!(FIND_DOT_BODY, b".body");
finder!(FIND_DOT_QUERY, b".query");
finder!(FIND_DOT_PARAMS, b".params");
finder!(FIND_GET_PARAMETER, b"getParameter");
finder!(FIND_GET_INPUT_STREAM, b"getInputStream");
finder!(FIND_POST_FORM, b"PostForm");
finder!(FIND_GET_HEADER, b"getHeader");
finder!(FIND_USER_INPUT, b"user_input");
finder!(FIND_USER_INPUT_CAMEL, b"userInput");
finder!(FIND_USER_DATA, b"user_data");
finder!(FIND_USER_DATA_CAMEL, b"userData");
finder!(FIND_PAYLOAD, b"payload");
