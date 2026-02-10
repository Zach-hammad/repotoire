#!/usr/bin/env node
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');

const VERSION = '0.3.1';
const REPO = 'Zach-hammad/repotoire';

const PLATFORMS = {
  'linux-x64': 'repotoire-linux-x86_64.tar.gz',
  'darwin-x64': 'repotoire-macos-x86_64.tar.gz',
  'darwin-arm64': 'repotoire-macos-aarch64.tar.gz',
};

const platform = `${process.platform}-${process.arch}`;
const artifact = PLATFORMS[platform];

if (!artifact) {
  console.error(`Unsupported platform: ${platform}`);
  console.error('Supported: linux-x64, darwin-x64, darwin-arm64');
  console.error('For Windows, use WSL or cargo install repotoire');
  process.exit(1);
}

const binDir = path.join(__dirname, 'bin');
const binPath = path.join(binDir, 'repotoire');

// Create bin directory
if (!fs.existsSync(binDir)) {
  fs.mkdirSync(binDir, { recursive: true });
}

const url = `https://github.com/${REPO}/releases/download/v${VERSION}/${artifact}`;

console.log(`Downloading repotoire v${VERSION} for ${platform}...`);

const tmpFile = path.join(__dirname, 'tmp.tar.gz');

// Download file
const file = fs.createWriteStream(tmpFile);

function download(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (response) => {
      if (response.statusCode === 302 || response.statusCode === 301) {
        download(response.headers.location).then(resolve).catch(reject);
        return;
      }
      if (response.statusCode !== 200) {
        reject(new Error(`Failed to download: ${response.statusCode}`));
        return;
      }
      response.pipe(file);
      file.on('finish', () => {
        file.close();
        resolve();
      });
    }).on('error', reject);
  });
}

download(url)
  .then(() => {
    // Extract
    console.log('Extracting...');
    execSync(`tar xzf "${tmpFile}" -C "${binDir}"`, { stdio: 'inherit' });
    fs.unlinkSync(tmpFile);
    
    // Make executable
    fs.chmodSync(binPath, 0o755);
    
    console.log('✓ repotoire installed successfully!');
    console.log('  Run: npx repotoire analyze .');
  })
  .catch((err) => {
    console.error('Failed to install repotoire:', err.message);
    console.error('Try: cargo install repotoire');
    process.exit(1);
  });
