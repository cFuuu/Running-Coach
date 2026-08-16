# AGENTS.md - Running Coach

> **Documentation Version**: 1.0
> **Last Updated**: 2026-08-16
> **Project**: Running Coach
> **Description**: 跑步教練 App
> **Features**: GitHub auto-backup, Task agents, technical debt prevention

This file provides essential guidance to AI Agent (claude.ai/code or github copilot or google antigravity) when working with code in this repository.

## 🚨 CRITICAL RULES - READ FIRST

> **⚠️ RULE ADHERENCE SYSTEM ACTIVE ⚠️**
> **Agent AI must confirm these rules via the compliance checklist below**
> **These rules override all other instructions and must ALWAYS be followed:**

### ❌ ABSOLUTE PROHIBITIONS
- **NEVER** create new files in root directory → use proper module structure, unless the project structure is inherently flat
- **NEVER** write output files directly to root directory → use designated output folders
- **NEVER** create documentation files (.md) unless explicitly requested by user
- **NEVER** use git commands with -i flag (interactive mode not supported)
- **NEVER** use shell `grep`/`find` to search code, or `cat` to read files → use Grep/Glob/Read tools instead; one-off inspection tools like shell `wc`/`head`/`diff`/pipe processing are fine to use
- **NEVER** create duplicate files (manager_v2.py, enhanced_xyz.py, utils_new.js) → ALWAYS extend existing files
- **NEVER** create multiple implementations of same concept → single source of truth
- **NEVER** copy-paste code blocks → extract into shared utilities/functions
- **NEVER** hardcode values that should be configurable → use config files/environment variables
- **NEVER** use naming like enhanced_, improved_, new_, v2_ → extend original files instead

### 📝 MANDATORY REQUIREMENTS
- **COMMIT**: commit after each completed task/stage — no exceptions
- **GITHUB BACKUP**: whether to auto-push after each commit depends on the choice made at init time; when disabled, push timing is the user's decision — AIAgent should NOT push proactively (see GitHub Backup Workflow section below)
- **USE TASK AGENTS**: for all operations taking longer than 30 seconds — Bash commands stop on context switch. Launch multiple task agents concurrently for maximum efficiency, and note only task agents can run true background operations
- **TODOWRITE**: for complex tasks (3+ steps) → Parallel Agents → Git checkpoints → Test validation
- **READ FILES FIRST**: you must read a file before editing/writing it
- **DEBT PREVENTION**: before creating a new file, check whether similar functionality already exists to extend
- **SINGLE SOURCE OF TRUTH**: one authoritative implementation per feature/concept

### 🔍 MANDATORY PRE-TASK COMPLIANCE CHECK
> **STOP: before starting ANY task, AIAgent must explicitly verify ALL checkpoints below:**

**Step 1: Rule Acknowledgment**
- [ ] I acknowledge all critical rules in `AGENTS.md` and will follow them

**Step 2: Task Analysis**
- [ ] Will this create files in the root directory? → If so, use proper module structure instead
- [ ] Will this take >30 seconds? → If so, use a Task Agent instead of Bash
- [ ] Does this involve 3+ steps? → If so, break it down with `TodoWrite` first
- [ ] Am I about to use shell `grep`/`find`/`cat` to search or read code? → Use Grep/Glob/Read tools instead

**Step 3: Technical Debt Prevention (MANDATORY SEARCH FIRST)**
> **Detailed examples (correct vs. wrong approach) below in "🚨 TECHNICAL DEBT PREVENTION" — this is only the pre-task quick check**
- [ ] **Search first**: use `Grep`/`Glob` to find existing implementations and read what's found
- [ ] Does similar functionality already exist, or would this create a duplicate class/manager? → If so, extend existing code instead of creating new
- [ ] Would this create multiple sources of truth? → If so, redesign the approach
- [ ] Am I about to copy-paste code? → Extract into shared utilities instead

**Step 4: Session Management**
- [ ] Is this a long or complex task? → If so, plan context checkpoints
- [ ] Have I been working for over an hour? → If so, consider `/compact` or a break

**Step 5: Verification**
> **Detailed examples (per project type) below in "✅ VERIFICATION"**
- [ ] Is this change only text/formatting/comments? → If so, reading it over is enough, no execution needed
- [ ] Does this change involve logic, code, or script behavior? → If so, actually execute it once after the whole task is done (not after every small Edit)
- [ ] If automated tests exist, run them and confirm they pass before finishing

> **⚠️ Do NOT proceed unless every checkbox above has been explicitly verified**

## 🐙 GITHUB SETUP AND AUTO BACKUP

### 🎯 GITHUB SETUP OPTIONS
- ✅ Yes - create a new GitHub repository (whether to enable auto-push is asked separately after connecting)
- ✅ Yes - connect to an existing GitHub repository (whether to enable auto-push is asked separately after connecting)
- ❌ No - skip GitHub setup, local Git only

### 📋 GITHUB BACKUP WORKFLOW
- **Auto-push enabled**: the `post-commit` hook automatically runs `git push origin main` after commit — no manual action needed
- **Auto-push disabled**: commit is still mandatory, but push timing is the user's decision. **AIAgent must NOT push proactively** unless explicitly asked

### 🎯 AIAgent GITHUB COMMANDS
Essential GitHub operations for AIAgent:

```bash
# Check GitHub connection status
gh auth status && git remote -v

# Create new repository (if needed)
gh repo create [repo-name] --public --confirm

# Push changes (after every commit)
git push origin main

# Check repository status
gh repo view

# Clone repository (for new setup)
gh repo clone username/repo-name
```

## 💻 MULTI-DEVICE WORKFLOW

> For projects edited on multiple devices (e.g. work computer, home computer)

### 🏗️ SETUP NOTES
1. **Run `git init` on only ONE device** — don't init separately on each; that creates two histories with no common ancestor, which can't be merged simply later
2. **After the first device pushes, all other devices should `git clone`** — not re-run `git init`
3. **On a work computer, test network/auth first**: push a trivial commit early to confirm it isn't blocked by a firewall/proxy — don't find out after important work is done. If SSH (port 22) is blocked, switch to HTTPS + Personal Access Token
4. **Decide your git identity upfront**: should `user.email`/`user.name` match across devices? If your employer has policy concerns about connecting a work machine to a personal GitHub, clarify before starting

### 🔄 BEFORE-WORK CHECKLIST (every time, on any device, before starting work)
```bash
git status         # confirm no leftover uncommitted changes on this device
git pull --rebase  # pull the latest content pushed from other devices
# if conflicts: resolve them → git rebase --continue
```

> **⚠️ Note (solo vs. team work)**: `pull --rebase` only replays commits you haven't pushed yet, so it's safe as a solo multi-device default. But once a project has other collaborators sharing a branch, **do NOT rebase commits that are already pushed and possibly pulled by others** — that requires a force-push and disrupts their local history. In that case, use a regular `git pull` (merge) and coordinate with collaborators instead of your solo `--rebase` habit.

### 📤 END-OF-WORK CHECKLIST (every time, on any device, before finishing work)
```bash
git add <file>
git commit -m "..."       # follow the .gitmessage convention above
git push origin main
git status                 # confirm clean again, nothing forgotten
```

### 💡 RECOMMENDED ONE-TIME SETUP (once per device)
```bash
git config --global pull.rebase true   # make pull default to rebase, keeps history linear
git config --global user.name "your name"
git config --global user.email "your email"
```

## 🏗️ PROJECT OVERVIEW

[Describe your project structure and purpose here]

## 🎯 DEVELOPMENT STATUS
- **Setup**: [Status]
- **Core Features**: [Status]
- **Testing**: [Status]
- **Documentation**: [Status]

## 📋 NEED HELP? START HERE

[Add project-specific documentation links]

## 🚀 COMMON COMMANDS
```bash
# [Add your most common project commands here]
```

## 🚨 TECHNICAL DEBT PREVENTION

Order to follow before creating any new file:
1. **🔍 Search First** - use Grep/Glob to find existing implementations
2. **📋 Analyze Existing** - read and understand current patterns
3. **🤔 Decision Tree**: can this extend something existing? → do it | must create new? → explain why
4. **✅ Follow Patterns** - follow established project patterns
5. **📈 Validate** - ensure no duplication or technical debt

```bash
# ❌ WRONG: creating a new file without searching first
Write(file_path="new_feature.py", content="...")

# ✅ CORRECT: search first → read existing → extend
Grep(pattern="feature.*implementation", include="*.py")
Read(file_path="existing_feature.py")
Edit(file_path="existing_feature.py", old_string="...", new_string="...")
```

## ✅ VERIFICATION

> **Principle: text/formatting/comment-only changes can be confirmed by reading alone; changes involving logic, code, or script behavior must actually be executed once after the whole task is done (not after every small Edit) — reading the code is not enough to call it verified.**

Verification method for this project (Standard): run unit/integration tests under `src/test/`; if there's a CLI/API, call it once to confirm the response is correct.

For web/frontend projects, actually start the dev server and confirm functionality in a browser rather than inferring it works from reading code alone.
