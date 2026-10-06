"""Local entry point: `python app.py` (also used by start.bat/start.sh)."""
import uvicorn

try:
    from dotenv import load_dotenv
    load_dotenv()  # gitignored .env -> os.environ (never override real env)
except ImportError:
    pass

from app.main import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
