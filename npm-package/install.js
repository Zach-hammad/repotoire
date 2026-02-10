#!/usr/bin/env node
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');

const VERSION = '0.3.2';
const REPO = 'Zach-hammad/repotoire';

const PLATFORMS = {
  'linux-x64': { file: 'repotoire-linux-x86_64.tar.gz', ext: 'tar.gz' },
  'darwin-x64': { file: 'repotoire-macos-aarch64.tar.gz', ext: 'tar.gz' }, // Rosetta 2
  'darwin-arm64': { file: 'repotoire-macos-aarch64.tar.gz', ext: 'tar.gz' },
  'win32-x64': { file: 'repotoire-windows-x86_64.zip', ext: 'zip' },
};

const platform = `${process.platform}-${process.arch}`;
const info = PLATFORMS[platform];

if (!info) {
  console.error(`Unsupported platform: ${platform}`);
  console.error('Supported: linux-x64, darwin-x64, darwin-arm64, win32-x64');
  process.exit(1);
}

const binDir = path.join(__dirname, 'bin');
const binName = process.platform === 'win32' ? 'repotoire.exe' : 'repotoire';
const binPath = path.join(binDir, binName);

if (!fs.existsSync(binDir)) {
  fs.mkdirSync(binDir, { recursive: true });
}

const url = `https://github.com/${REPO}/releases/download/v${VERSION}/${info.file}`;
const tmpFile = path.join(__dirname, `tmp.${info.ext}`);

console.log(`Downloading repotoire v${VERSION} for ${platform}...`);

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
      const file = fs.createWriteStream(tmpFile);
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
    console.log('Extracting...');
    
    if (info.ext === 'tar.gz') {
      execSync(`tar xzf "${tmpFile}" -C "${binDir}"`, { stdio: 'inherit' });
    } else if (info.ext === 'zip') {
      // Windows: use PowerShell to extract
      if (process.platform === 'win32') {
        execSync(`powershell -command "Expand-Archive -Force '${tmpFile}' '${binDir}'"`, { stdio: 'inherit' });
      } else {
        execSync(`unzip -o "${tmpFile}" -d "${binDir}"`, { stdio: 'inherit' });
      }
    }
    
    fs.unlinkSync(tmpFile);
    
    // Make executable (not needed on Windows)
    if (process.platform !== 'win32') {
      fs.chmodSync(binPath, 0o755);
    }
    
    console.log('✓ repotoire installed successfully!');
    console.log('  Run: npx repotoire analyze .');
  })
  .catch((err) => {
    console.error('Failed to install repotoire:', err.message);
    console.error('Try: cargo install repotoire');
    process.exit(1);
  });
