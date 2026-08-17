# AGENTS.md - Running Coach

> **Documentation Version**: 1.0
> **Last Updated**: 2026-08-16
> **Project**: Running Coach
> **Description**: 跑步教練 App

This file provides **project-specific** guidance to AI Agent when working with code in this repository. General working habits — commit message convention, root-directory/duplicate-file/naming prohibitions, tool selection (Grep/Glob/Read), read-before-edit, Task Agent for >30s ops, TodoWrite for 3+ step tasks, technical debt prevention order, multi-device workflow, and the text-vs-logic verification principle — are already defined in the user's global `CLAUDE.md` and auto-loaded into every project, so they are **not** repeated here. Only what's specific to Running Coach is listed below.

## ❌ Additional Prohibitions (not already covered globally)
- **NEVER** hardcode values that should be configurable → use config files/environment variables
- **NEVER** copy-paste code blocks → extract into shared utilities/functions

## 🐙 GitHub Setup for This Project
**Status**: not yet connected — local Git only, per the user's choice at init time.

### Setup Options (when the user is ready)
- Create a new GitHub repository, or connect to an existing one — whether to enable auto-push is asked separately after connecting
- Stay local-only

### Auto-Push Mechanism (once GitHub is connected)
- **Enabled**: a `post-commit` hook runs `git push origin main` automatically after every commit
- **Disabled (default)**: commit is still mandatory, but push timing stays the user's call — do not push proactively (see global "Push Timing" rule)

### Commands Reference
```bash
gh auth status && git remote -v      # check connection status
gh repo create [repo-name] --public --confirm
git push origin main
gh repo view
gh repo clone username/repo-name
```

## 🐍 Python Environment (IMPORTANT)

This project uses a dedicated conda environment named **`rc`** (Python 3.12).

**Do NOT use the bare `python` command** — on this machine it resolves to an unrelated venv (`hermes-agent`) that has no pip and none of this project's packages. `pip` on PATH also points at a *different* Python 3.10 install, so `pip install X` followed by `python -c "import X"` will fail confusingly.

Always invoke the environment's interpreter by absolute path:

```bash
# Bash tool
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v
"$RC" -m src.main.python.services.garmin_import_runner --help
```

```powershell
# PowerShell tool (conda is not on PATH; call it by absolute path too)
& "C:\Users\cFu\anaconda3\envs\rc\python.exe" --version
& "C:\Users\cFu\anaconda3\Scripts\conda.exe" env list
```

`conda activate rc` works in the user's own interactive terminal, but **not** across tool calls — each Bash/PowerShell invocation is a fresh shell, so activation does not persist. Use the absolute path instead.

Dependencies: `requirements.txt` (single source of truth for versions) and `environment.yml` (conda wrapper that pip-installs from requirements.txt). Recreate with `conda env create -f environment.yml`.

## ✅ Verification Method for This Project
Run unit/integration tests under `src/test/`; if there's a CLI/API, call it once to confirm the response is correct. If a web front-end is added later, start the dev server and confirm functionality in a browser rather than inferring it from reading code alone.

## 🏗️ Project Overview

[Describe your project structure and purpose here]

## 🎯 Development Status
- **Setup**: [Status]
- **Core Features**: [Status]
- **Testing**: [Status]
- **Documentation**: [Status]

## 📋 Need Help? Start Here

[Add project-specific documentation links]

## 🚀 Common Commands
```bash
# [Add your most common project commands here]
```
