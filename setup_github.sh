#!/bin/bash
# Setup script to initialize git and push to GitHub
#
# Usage:
#   1. Create a new repository on GitHub (don't initialize with README)
#   2. Copy the repository URL (https://github.com/yourusername/your-repo.git)
#   3. Run this script: ./setup_github.sh https://github.com/yourusername/your-repo.git

if [ $# -eq 0 ]; then
    echo "Usage: ./setup_github.sh <github-repo-url>"
    echo "Example: ./setup_github.sh https://github.com/yourusername/hiv-model.git"
    exit 1
fi

REPO_URL=$1

echo "Setting up git repository..."

# Remove any lock files
rm -f .git/index.lock 2>/dev/null

# Check if git is already initialized
if [ ! -d ".git" ]; then
    echo "Initializing git repository..."
    git init
else
    echo "Git repository already initialized."
fi

# Add files
echo "Staging files..."
git add .gitignore README_GITHUB.md
git add *.py config_v6_rel_pop.json documentation/

# Create initial commit if needed
if git rev-parse HEAD >/dev/null 2>&1; then
    echo "Commit already exists."
else
    echo "Creating initial commit..."
    git commit -m "Initial commit: HIV transmission model v3

- Demographic processes (births, aging, mortality)
- HIV transmission with population stratification
- Fractional and discrete aging methods
- Population adjustments for calibration
- Netherlands data (1970-2020)
- Comprehensive documentation

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
fi

# Rename branch to main (GitHub standard)
git branch -M main

# Add remote
echo "Adding remote repository..."
git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL"

# Push to GitHub
echo "Pushing to GitHub..."
git push -u origin main

echo ""
echo "✅ Done! Your repository is now on GitHub at:"
echo "   $REPO_URL"
echo ""
echo "Your colleague can clone it with:"
echo "   git clone $REPO_URL"
echo "   cd $(basename $REPO_URL .git)"
echo "   python3 run_v3.py config_v6_rel_pop.json"
