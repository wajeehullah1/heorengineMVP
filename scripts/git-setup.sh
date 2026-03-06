#!/bin/bash
set -e

# Initialize git first — required before any git commands
if [ ! -d .git ]; then
    echo "📦 Initializing git repository..."
    git init
    git branch -M main
else
    echo "✅ Git already initialized"
fi

echo ""
echo "🔒 Checking for sensitive data..."

# Check if .env files exist and warn
if [ -f .env ]; then
    echo "⚠️  WARNING: .env file found"
    echo "   Confirming .gitignore covers it..."
    if git check-ignore -q .env; then
        echo "   ✅ .env is ignored by git — safe to proceed"
    else
        echo "   ❌ .env is NOT in .gitignore! Add it before committing."
        exit 1
    fi
fi

# Scan all source files directly for API keys (no git ls-files needed)
echo "   Scanning source files for API keys..."
if grep -r --include="*.py" --include="*.js" --include="*.jsx" \
          --include="*.ts" --include="*.tsx" --include="*.json" \
          --include="*.txt" --include="*.md" --include="*.sh" \
          -l "sk-ant-" . 2>/dev/null | grep -v ".git"; then
    echo "❌ ERROR: API key found in the files above!"
    echo "   Remove all API keys before committing."
    exit 1
fi

echo "✅ No API keys detected in source files"
echo ""

# Stage files
echo "📝 Staging files..."
git add .

# Show what will be committed
echo ""
echo "Files to be committed:"
git status --short

echo ""
echo "⚠️  FINAL CHECK: Review the files above"
echo "   Press Ctrl+C to cancel, Enter to commit"
read

# Commit
echo "💾 Creating commit..."
git commit -m "Initial commit: HEOR Engine MVP" || echo "Nothing to commit"

echo ""
echo "✅ Git setup complete!"
echo ""
echo "Next steps:"
echo "1. Create a new repository on GitHub"
echo "2. Run: git remote add origin YOUR_GITHUB_URL"
echo "3. Run: git push -u origin main"
