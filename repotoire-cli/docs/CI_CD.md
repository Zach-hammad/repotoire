# CI/CD Integration

Integrate Repotoire into your continuous integration pipeline to catch issues before they reach production.

## Table of Contents

- [Quick Start](#quick-start)
- [GitHub Actions](#github-actions)
- [GitLab CI](#gitlab-ci)
- [Jenkins](#jenkins)
- [CircleCI](#circleci)
- [Azure Pipelines](#azure-pipelines)
- [Pre-commit Hooks](#pre-commit-hooks)
- [Best Practices](#best-practices)

---

## Quick Start

The key options for CI:

```bash
# Basic CI run
repotoire analyze . --fail-on critical --no-emoji

# Options:
#   --fail-on <LEVEL>   Exit code 1 if findings at this severity or higher
#   --no-emoji          Clean output for CI logs
#   --format json       Machine-readable output
#   --output FILE       Save report to file
```

---

## GitHub Actions

### Basic Workflow

```yaml
# .github/workflows/code-quality.yml
name: Code Quality

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  repotoire:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Repotoire
        run: |
          curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
          sudo mv repotoire /usr/local/bin/
      
      - name: Run Code Analysis
        run: repotoire analyze . --fail-on critical --no-emoji
```

### With SARIF Upload (Code Scanning)

GitHub can display findings directly in pull requests using SARIF:

```yaml
# .github/workflows/code-quality.yml
name: Code Quality

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  repotoire:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
      contents: read
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Repotoire
        run: |
          curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
          sudo mv repotoire /usr/local/bin/
      
      - name: Run Code Analysis
        run: repotoire analyze . --format sarif --output results.sarif
        continue-on-error: true
      
      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: results.sarif
      
      - name: Fail on Critical
        run: repotoire analyze . --fail-on critical --no-emoji
```

### With Caching

Speed up repeated runs:

```yaml
jobs:
  repotoire:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history for git analysis
      
      - name: Cache Repotoire Data
        uses: actions/cache@v4
        with:
          path: .repotoire
          key: repotoire-${{ runner.os }}-${{ hashFiles('**/*.py', '**/*.js', '**/*.ts') }}
          restore-keys: |
            repotoire-${{ runner.os }}-
      
      - name: Install Repotoire
        run: |
          curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
          sudo mv repotoire /usr/local/bin/
      
      - name: Run Analysis
        run: repotoire analyze . --fail-on high --no-emoji
```

### PR Comment with Results

Post findings as a comment on pull requests:

```yaml
jobs:
  repotoire:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Repotoire
        run: |
          curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
          sudo mv repotoire /usr/local/bin/
      
      - name: Run Analysis
        id: analyze
        run: |
          repotoire analyze . --format markdown --output report.md --no-emoji
          echo "report<<EOF" >> $GITHUB_OUTPUT
          cat report.md >> $GITHUB_OUTPUT
          echo "EOF" >> $GITHUB_OUTPUT
        continue-on-error: true
      
      - name: Comment PR
        uses: actions/github-script@v7
        if: github.event_name == 'pull_request'
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: `## 🎼 Repotoire Report\n\n${{ steps.analyze.outputs.report }}`
            })
      
      - name: Fail on Critical
        run: repotoire analyze . --fail-on critical --no-emoji
```

---

## GitLab CI

### Basic Pipeline

```yaml
# .gitlab-ci.yml
stages:
  - quality

code-quality:
  stage: quality
  image: rust:latest
  before_script:
    - curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
    - mv repotoire /usr/local/bin/
  script:
    - repotoire analyze . --fail-on critical --no-emoji
  cache:
    paths:
      - .repotoire/
```

### With Code Quality Report

GitLab can display code quality reports in merge requests:

```yaml
# .gitlab-ci.yml
code-quality:
  stage: quality
  image: rust:latest
  before_script:
    - curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
    - mv repotoire /usr/local/bin/
  script:
    - repotoire analyze . --format json --output gl-code-quality-report.json --no-emoji
    - repotoire analyze . --fail-on high --no-emoji
  artifacts:
    reports:
      codequality: gl-code-quality-report.json
    paths:
      - gl-code-quality-report.json
    expire_in: 1 week
  cache:
    paths:
      - .repotoire/
```

### Merge Request Only

```yaml
code-quality:
  stage: quality
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  # ... rest of config
```

---

## Jenkins

### Declarative Pipeline

```groovy
// Jenkinsfile
pipeline {
    agent any
    
    stages {
        stage('Install Repotoire') {
            steps {
                sh '''
                    curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
                    chmod +x repotoire
                '''
            }
        }
        
        stage('Code Analysis') {
            steps {
                sh './repotoire analyze . --format json --output report.json --no-emoji'
                sh './repotoire analyze . --fail-on critical --no-emoji'
            }
            post {
                always {
                    archiveArtifacts artifacts: 'report.json', fingerprint: true
                }
            }
        }
    }
}
```

### With HTML Report

```groovy
pipeline {
    agent any
    
    stages {
        stage('Install') {
            steps {
                sh '''
                    curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
                    chmod +x repotoire
                '''
            }
        }
        
        stage('Analyze') {
            steps {
                sh './repotoire analyze . --format html --output report.html --no-emoji'
            }
            post {
                always {
                    publishHTML(target: [
                        allowMissing: false,
                        alwaysLinkToLastBuild: true,
                        keepAll: true,
                        reportDir: '.',
                        reportFiles: 'report.html',
                        reportName: 'Repotoire Report'
                    ])
                }
            }
        }
        
        stage('Quality Gate') {
            steps {
                sh './repotoire analyze . --fail-on critical --no-emoji'
            }
        }
    }
}
```

### Scripted Pipeline

```groovy
node {
    stage('Checkout') {
        checkout scm
    }
    
    stage('Install Repotoire') {
        sh '''
            curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
            chmod +x repotoire
        '''
    }
    
    stage('Code Quality') {
        def status = sh(
            script: './repotoire analyze . --fail-on critical --no-emoji',
            returnStatus: true
        )
        
        if (status != 0) {
            currentBuild.result = 'UNSTABLE'
            error('Critical code quality issues found!')
        }
    }
}
```

---

## CircleCI

### Basic Config

```yaml
# .circleci/config.yml
version: 2.1

jobs:
  code-quality:
    docker:
      - image: cimg/base:stable
    steps:
      - checkout
      - run:
          name: Install Repotoire
          command: |
            curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
            sudo mv repotoire /usr/local/bin/
      - run:
          name: Run Analysis
          command: repotoire analyze . --fail-on critical --no-emoji
      - store_artifacts:
          path: .repotoire
          destination: analysis-cache

workflows:
  main:
    jobs:
      - code-quality
```

### With Caching

```yaml
version: 2.1

jobs:
  code-quality:
    docker:
      - image: cimg/base:stable
    steps:
      - checkout
      
      - restore_cache:
          keys:
            - repotoire-{{ checksum "Cargo.lock" }}
            - repotoire-
      
      - run:
          name: Install Repotoire
          command: |
            if [ ! -f /usr/local/bin/repotoire ]; then
              curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
              sudo mv repotoire /usr/local/bin/
            fi
      
      - run:
          name: Run Analysis
          command: repotoire analyze . --format json --output report.json --no-emoji
      
      - run:
          name: Quality Gate
          command: repotoire analyze . --fail-on critical --no-emoji
      
      - save_cache:
          paths:
            - .repotoire
          key: repotoire-{{ checksum "Cargo.lock" }}
      
      - store_artifacts:
          path: report.json

workflows:
  quality:
    jobs:
      - code-quality
```

### Orb (Reusable)

```yaml
# Create as: .circleci/orbs/repotoire.yml
version: 2.1

description: Repotoire code quality analysis

commands:
  analyze:
    parameters:
      fail_on:
        type: string
        default: "critical"
    steps:
      - run:
          name: Install Repotoire
          command: |
            curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
            sudo mv repotoire /usr/local/bin/
      - run:
          name: Run Analysis
          command: repotoire analyze . --fail-on << parameters.fail_on >> --no-emoji

jobs:
  quality-check:
    docker:
      - image: cimg/base:stable
    parameters:
      fail_on:
        type: string
        default: "critical"
    steps:
      - checkout
      - analyze:
          fail_on: << parameters.fail_on >>
```

---

## Azure Pipelines

```yaml
# azure-pipelines.yml
trigger:
  - main

pool:
  vmImage: 'ubuntu-latest'

steps:
  - checkout: self
    fetchDepth: 0

  - script: |
      curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
      sudo mv repotoire /usr/local/bin/
    displayName: 'Install Repotoire'

  - script: repotoire analyze . --format sarif --output $(Build.ArtifactStagingDirectory)/results.sarif --no-emoji
    displayName: 'Run Analysis'
    continueOnError: true

  - task: PublishBuildArtifacts@1
    inputs:
      PathtoPublish: '$(Build.ArtifactStagingDirectory)'
      ArtifactName: 'CodeAnalysis'

  - script: repotoire analyze . --fail-on critical --no-emoji
    displayName: 'Quality Gate'
```

---

## Pre-commit Hooks

### Using pre-commit framework

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: repotoire
        name: Repotoire Code Quality
        entry: repotoire analyze . --relaxed --no-emoji
        language: system
        pass_filenames: false
        stages: [pre-commit]
```

Install:
```bash
pip install pre-commit
pre-commit install
```

### Using Git hooks directly

```bash
# .git/hooks/pre-commit
#!/bin/bash

echo "Running Repotoire analysis..."

if ! repotoire analyze . --fail-on critical --no-emoji --relaxed; then
    echo ""
    echo "❌ Critical issues found. Fix them before committing."
    echo "   Run 'repotoire findings' to see details."
    exit 1
fi

echo "✅ Code quality check passed"
```

Make it executable:
```bash
chmod +x .git/hooks/pre-commit
```

### Pre-push Hook

For longer analysis on push:

```bash
# .git/hooks/pre-push
#!/bin/bash

echo "Running full Repotoire analysis..."

if ! repotoire analyze . --fail-on high --no-emoji; then
    echo ""
    echo "❌ High-severity issues found. Fix them before pushing."
    exit 1
fi
```

---

## Best Practices

### 1. Start Lenient, Get Stricter

Begin with `--fail-on critical`, then gradually raise the bar:

```yaml
# Week 1-2: Block only critical
- run: repotoire analyze . --fail-on critical

# Week 3-4: Block high and above
- run: repotoire analyze . --fail-on high

# Eventually: Block medium and above
- run: repotoire analyze . --fail-on medium
```

### 2. Use `--relaxed` for Speed

In pre-commit hooks, use `--relaxed` for faster feedback:

```bash
repotoire analyze . --relaxed  # Only high/critical, faster
```

### 3. Cache Analysis Data

Cache `.repotoire/` directory between runs:

```yaml
# GitHub Actions
- uses: actions/cache@v4
  with:
    path: .repotoire
    key: repotoire-${{ hashFiles('**/*.py', '**/*.js') }}
```

### 4. Skip Git Analysis in CI

If git history analysis is slow:

```bash
repotoire analyze . --no-git
```

### 5. Generate Reports

Always generate a report artifact:

```bash
repotoire analyze . --format json --output report.json
repotoire analyze . --format html --output report.html
```

### 6. Use SARIF for GitHub

SARIF integrates findings directly into GitHub's Security tab and PR diffs.

### 7. Baseline for Existing Projects

For legacy codebases, skip existing findings and only fail on new ones:

```bash
# First run: establish baseline
repotoire analyze . --format json --output baseline.json

# CI: compare against baseline (future feature)
# For now, start with --relaxed and improve gradually
```

### 8. Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (no findings above threshold) |
| 1 | Findings at/above `--fail-on` severity |
| 2 | Error during analysis |

Use in scripts:

```bash
repotoire analyze . --fail-on critical
if [ $? -eq 1 ]; then
    echo "Quality gate failed!"
    exit 1
fi
```

---

## Troubleshooting

### "cmake not found" during install

Pre-built binaries don't need cmake. Use the curl/tar install:

```bash
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
```

### Analysis taking too long

```bash
# Skip git history
repotoire analyze . --no-git

# Use relaxed mode
repotoire analyze . --relaxed

# Increase workers
repotoire analyze . --workers 16
```

### "No such file: .repotoire"

Run `repotoire clean` to reset, then re-run analysis:

```bash
repotoire clean
repotoire analyze .
```
