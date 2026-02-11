# GitHub Setup Guide

This guide will help you upload your HIV model to GitHub so your colleague can easily download and run it.

## Quick Start (Easiest Method)

### Step 1: Create GitHub Repository

1. Go to https://github.com/new
2. Repository name: `hiv-model-netherlands` (or any name you prefer)
3. Description: "HIV transmission model for Netherlands population (1970-2020)"
4. **Important:** Keep repository **Public** (so colleague can access) or **Private** (and add colleague as collaborator)
5. **Do NOT check** "Add a README file"
6. **Do NOT check** "Add .gitignore"
7. **Do NOT choose** a license yet
8. Click **"Create repository"**

### Step 2: Copy Repository URL

After creating, GitHub will show you a URL like:
```
https://github.com/yourusername/hiv-model-netherlands.git
```

**Copy this URL!**

### Step 3: Run Setup Script

**On Mac/Linux:**
```bash
cd /path/to/v3_patch
./setup_github.sh https://github.com/yourusername/hiv-model-netherlands.git
```

**On Windows:**
```cmd
cd C:\path\to\v3_patch
setup_github.bat https://github.com/yourusername/hiv-model-netherlands.git
```

The script will:
- Initialize git
- Stage all necessary files
- Create initial commit
- Push to GitHub

### Step 4: Share with Colleague

Send your colleague the repository URL:
```
https://github.com/yourusername/hiv-model-netherlands
```

They can clone and run with:
```bash
git clone https://github.com/yourusername/hiv-model-netherlands.git
cd hiv-model-netherlands
pip install numpy pandas --break-system-packages
python3 run_v3.py config_v6_rel_pop.json
```

---

## Manual Method (If Scripts Don't Work)

### Step 1: Initialize Git

```bash
cd /path/to/v3_patch
git init
```

### Step 2: Stage Files

```bash
git add .gitignore README_GITHUB.md
git add *.py config_v6_rel_pop.json documentation/
```

### Step 3: Create Commit

```bash
git commit -m "Initial commit: HIV transmission model v3

- Demographic processes (births, aging, mortality)
- HIV transmission with population stratification
- Fractional and discrete aging methods
- Population adjustments for calibration
- Netherlands data (1970-2020)
- Comprehensive documentation

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

### Step 4: Rename Branch to Main

```bash
git branch -M main
```

### Step 5: Add Remote and Push

```bash
git remote add origin https://github.com/yourusername/hiv-model-netherlands.git
git push -u origin main
```

---

## Troubleshooting

### Problem: "remote: Repository not found"

**Solution:** Make sure you created the repository on GitHub first and copied the correct URL.

### Problem: "Permission denied (publickey)"

**Solution:** You need to set up SSH keys or use HTTPS with username/password:

**Option A: Use HTTPS (easier)**
```bash
git remote set-url origin https://github.com/yourusername/hiv-model-netherlands.git
```
When prompted, enter your GitHub username and password (or personal access token).

**Option B: Set up SSH keys**
Follow GitHub's guide: https://docs.github.com/en/authentication/connecting-to-github-with-ssh

### Problem: ".git/index.lock exists"

**Solution:** Remove the lock file:
```bash
rm -f .git/index.lock
```

Then try the commit again.

### Problem: "fatal: not a git repository"

**Solution:** You're in the wrong directory. Navigate to v3_patch:
```bash
cd /path/to/v3_patch
```

---

## Adding Colleague as Collaborator (Private Repo)

If you made the repository **private**, you need to add your colleague:

1. Go to your repository on GitHub
2. Click **Settings** (top right)
3. Click **Collaborators** (left sidebar)
4. Click **Add people**
5. Enter colleague's GitHub username or email
6. Click **Add [username] to this repository**

They'll receive an email invitation.

---

## What Gets Uploaded

### ✅ Included Files
- All Python source code (`*.py`)
- Main configuration: `config_v6_rel_pop.json`
- Documentation folder (all patch notes)
- README with instructions
- Setup scripts

### ❌ Excluded Files (via .gitignore)
- Test configurations (`config_test*.json`)
- Output files (`*.csv`, `*.png`)
- Python cache (`__pycache__/`, `*.pyc`)
- IDE settings (`.vscode/`, `.idea/`)
- Temporary files

---

## Alternative: Share as ZIP

If you don't want to use GitHub, you can share as a ZIP file:

### Step 1: Create ZIP

**Mac/Linux:**
```bash
cd /path/to
zip -r hiv-model.zip v3_patch/ \
    -x "v3_patch/__pycache__/*" \
    -x "v3_patch/output/*" \
    -x "v3_patch/config_test*.json" \
    -x "v3_patch/*.pyc"
```

**Windows:** Right-click v3_patch folder → "Send to" → "Compressed (zipped) folder"

### Step 2: Share ZIP

Upload to:
- Google Drive
- Dropbox
- Email (if < 25MB)
- WeTransfer (for larger files)

Send link to colleague.

### Step 3: Colleague Extracts and Runs

```bash
unzip hiv-model.zip
cd v3_patch
pip install numpy pandas --break-system-packages
python3 run_v3.py config_v6_rel_pop.json
```

---

## Verifying Upload

After pushing to GitHub, verify by:

1. Go to your repository URL on GitHub
2. Check that you see:
   - `README_GITHUB.md` (displayed as homepage)
   - All `.py` files
   - `config_v6_rel_pop.json`
   - `documentation/` folder
3. Click on `README_GITHUB.md` to see the instructions

---

## Updating Repository Later

When you make changes and want to update GitHub:

```bash
git add .
git commit -m "Description of changes"
git push
```

Your colleague can update their copy with:
```bash
git pull
```

---

## Summary

**Fastest method:**
1. Create repo on GitHub (don't add README)
2. Copy the repository URL
3. Run `./setup_github.sh <url>` (Mac/Linux) or `setup_github.bat <url>` (Windows)
4. Share repo URL with colleague

**They run:**
```bash
git clone <url>
cd <repo-name>
pip install numpy pandas --break-system-packages
python3 run_v3.py config_v6_rel_pop.json
```

Done! 🎉

---

**Need help?** The setup scripts will display clear error messages if something goes wrong.
