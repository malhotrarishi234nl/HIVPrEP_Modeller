@echo off
REM Setup script to initialize git and push to GitHub (Windows)
REM
REM Usage:
REM   1. Create a new repository on GitHub (don't initialize with README)
REM   2. Copy the repository URL (https://github.com/yourusername/your-repo.git)
REM   3. Run this script: setup_github.bat https://github.com/yourusername/your-repo.git

if "%1"=="" (
    echo Usage: setup_github.bat ^<github-repo-url^>
    echo Example: setup_github.bat https://github.com/yourusername/hiv-model.git
    exit /b 1
)

set REPO_URL=%1

echo Setting up git repository...

REM Remove any lock files
del /f /q .git\index.lock 2>nul

REM Check if git is already initialized
if not exist ".git" (
    echo Initializing git repository...
    git init
) else (
    echo Git repository already initialized.
)

REM Add files
echo Staging files...
git add .gitignore README_GITHUB.md
git add *.py config_v6_rel_pop.json documentation/

REM Create initial commit if needed
git rev-parse HEAD >nul 2>&1
if errorlevel 1 (
    echo Creating initial commit...
    git commit -m "Initial commit: HIV transmission model v3" -m "- Demographic processes (births, aging, mortality)" -m "- HIV transmission with population stratification" -m "- Fractional and discrete aging methods" -m "- Population adjustments for calibration" -m "- Netherlands data (1970-2020)" -m "- Comprehensive documentation" -m "" -m "Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
) else (
    echo Commit already exists.
)

REM Rename branch to main (GitHub standard)
git branch -M main

REM Add remote
echo Adding remote repository...
git remote add origin %REPO_URL% 2>nul
if errorlevel 1 git remote set-url origin %REPO_URL%

REM Push to GitHub
echo Pushing to GitHub...
git push -u origin main

echo.
echo Done! Your repository is now on GitHub at:
echo    %REPO_URL%
echo.
echo Your colleague can clone it with:
echo    git clone %REPO_URL%
echo    cd [repository-name]
echo    python run_v3.py config_v6_rel_pop.json
