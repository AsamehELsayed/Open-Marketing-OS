@echo off
REM Open Marketing OS v0.1 — Windows start
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate
pip install -r requirements.txt
python app.py
