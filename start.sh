#!/bin/sh
# Open Marketing OS v0.1 — POSIX start
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
pip install -r requirements.txt
python app.py
