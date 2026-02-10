"""
⚠️ DEPRECATED: Repotoire has moved to Rust!

Install the new version:
    cargo install repotoire

Or download binary:
    https://github.com/Zach-hammad/repotoire/releases

Then run:
    repotoire analyze .
"""

import sys

def main():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║  ⚠️  REPOTOIRE HAS MOVED TO RUST!                                 ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  The Python version is deprecated. Install the new Rust version: ║
║                                                                  ║
║    cargo install repotoire                                       ║
║                                                                  ║
║  Or download binary:                                             ║
║    https://github.com/Zach-hammad/repotoire/releases             ║
║                                                                  ║
║  Why Rust?                                                       ║
║    • 10x faster (2s vs 20s)                                      ║
║    • 81 detectors (was 47)                                       ║
║    • 24MB binary, no dependencies                                ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
""")
    sys.exit(1)

if __name__ == "__main__":
    main()
