# Agent Task: Laptop Setup (Arch Linux)

## Context
- **Source**: Project transferred via rsync from another machine
- **Excluded**: `bong_data/wakeword_training/` (18G training data - not needed for runtime)
- **Included**: `venv/` (6.3G with patched discord-ext-voice-recv), all runtime data
- **Target**: Arch Linux laptop, user `eve`, path `/home/eve/Desktop/Python Discord Bot`

## Execution Steps

### Step 1: Verify Python Version
```bash
python3 --version
```
- **Expected**: `Python 3.14.5`
- **If different**: Proceed to Step 2B (recreate venv)
- **If matches**: Proceed to Step 2A (fix transferred venv)

### Step 2A: Fix Transferred venv (Python 3.14.5 only)
```bash
cd /home/eve/Desktop/Python\ Discord\ Bot

# Fix hardcoded source paths in venv scripts
find venv/bin -type f -exec sed -i 's|/home/eve/Desktop/Python Discord Bot|/home/eve/Desktop/Python Discord Bot|g' {} \;

# Fix Python interpreter path if needed
find venv/bin -type f -exec sed -i 's|/usr/bin/python3.14|/usr/bin/python3|g' {} \;

# Activate venv
source venv/bin/activate
```
**Then proceed to Step 3.**

### Step 2B: Recreate venv (if Python version differs)
```bash
cd /home/eve/Desktop/Python\ Discord\ Bot

# Remove transferred venv
rm -rf venv

# Create new venv
python3 -m venv venv

# Activate venv
source venv/bin/activate

# Install all dependencies from requirements.txt
pip install -r requirements.txt
```
**Then proceed to Step 3.**

### Step 3: Install System Dependencies (Arch Linux)
```bash
sudo pacman -S --noconfirm ffmpeg opus
```

### Step 4: Verify Installation
```bash
cd /home/eve/Desktop/Python\ Discord\ Bot
source venv/bin/activate

# Test critical imports
python -c "import discord; import langchain; import openwakeword; import faster_whisper; print('All imports OK')"

# Run test suite
pytest tests/ -v
```

### Step 5: Configure Environment
```bash
cd /home/eve/Desktop/Python\ Discord\ Bot

# Check if .env exists and has DISCORD_TOKEN
if ! grep -q "DISCORD_TOKEN" .env 2>/dev/null; then
    echo "DISCORD_TOKEN=<insert_token_here>" >> .env
fi
```

### Step 6: First Run Test
```bash
cd /home/eve/Desktop/Python\ Discord\ Bot
source venv/bin/activate
python src/main.py --help
```

## Success Criteria
- [ ] `pytest tests/ -v` passes
- [ ] `python src/main.py --help` shows CLI options without errors
- [ ] All imports in Step 4 succeed
- [ ] `bong_data/wakeword_models/hey_bong.onnx` exists
- [ ] `bong_data/chroma_db/` directory exists with data

## Notes
- `discord-ext-voice-recv` is installed from vocolboy GitHub fork (line 3 of requirements.txt)
- Memory CLI: `python -m bong_utilities.manage_memory`
- Use alias `bongenv` instead of `source venv/bin/activate` if available
- ChromaDB data persists in `bong_data/chroma_db/`
