# Homebrew Core PR Template

Submit this PR to https://github.com/Homebrew/homebrew-core when you have 50+ stars.

## PR Title
Add repotoire (graph-powered code analysis)

## Formula File
Save as `Formula/r/repotoire.rb`:

```ruby
class Repotoire < Formula
  desc "Graph-powered code analysis with 81 detectors for security and quality"
  homepage "https://github.com/Zach-hammad/repotoire"
  version "0.3.1"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/Zach-hammad/repotoire/releases/download/v#{version}/repotoire-macos-aarch64.tar.gz"
      sha256 "REPLACE_WITH_ACTUAL_SHA256"
    end
    on_intel do
      url "https://github.com/Zach-hammad/repotoire/releases/download/v#{version}/repotoire-macos-x86_64.tar.gz"
      sha256 "REPLACE_WITH_ACTUAL_SHA256"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/Zach-hammad/repotoire/releases/download/v#{version}/repotoire-linux-x86_64.tar.gz"
      sha256 "REPLACE_WITH_ACTUAL_SHA256"
    end
  end

  def install
    bin.install "repotoire"
  end

  test do
    assert_match "repotoire #{version}", shell_output("#{bin}/repotoire --version")
  end
end
```

## PR Description

**What does this formula install?**
Repotoire is a graph-powered code analysis CLI that builds a knowledge graph of your codebase to detect security vulnerabilities, architectural issues, and code smells.

**Features:**
- 81 built-in detectors (security, architecture, code quality)
- Supports 9 languages (Python, TypeScript, JavaScript, Go, Java, Rust, C/C++, C#)
- Pure Rust, single 24MB binary
- Analyzes 500 files in ~2 seconds

**Links:**
- GitHub: https://github.com/Zach-hammad/repotoire
- Crates.io: https://crates.io/crates/repotoire

## How to get SHA256

```bash
curl -L https://github.com/Zach-hammad/repotoire/releases/download/v0.3.1/repotoire-macos-aarch64.tar.gz | shasum -a 256
curl -L https://github.com/Zach-hammad/repotoire/releases/download/v0.3.1/repotoire-macos-x86_64.tar.gz | shasum -a 256
curl -L https://github.com/Zach-hammad/repotoire/releases/download/v0.3.1/repotoire-linux-x86_64.tar.gz | shasum -a 256
```
