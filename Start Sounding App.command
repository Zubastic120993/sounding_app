#!/bin/zsh
# Go to your project folder
cd "/Users/$USER/Desktop/sounding_app" || exit 1

# Activate your virtual environment
source "venv/bin/activate"

# Run the launcher
exec python main_launcher.py