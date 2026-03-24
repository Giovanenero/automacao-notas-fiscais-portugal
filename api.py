import os
import tempfile
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from fastapi.security import APIKeyHeader
from dotenv import load_dotenv

from main import analyze_file

load_dotenv()

app = FastAPI()

API_TOKEN = os.getenv("API_TOKEN")

api_key_header = APIKeyHeader(name="Authorization")

def check_token(api_key: str = Depends(api_key_header)):
    if api_key != API_TOKEN:
        raise HTTPException(401, "Token invalido")

@app.post("/analyze")
def analyze_invoice(
    documento: UploadFile = File(...),
    _: None = Depends(check_token)
) -> dict[str, Any]:

    if documento.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=400, detail="O documento precisa ser um PDF")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.join(temp_dir, documento.filename or "documento.pdf")
        with open(temp_path, "wb") as temp_file:
            temp_file.write(documento.file.read())

        resultado = analyze_file(temp_path)

    if not resultado:
        raise HTTPException(status_code=500, detail="Falha ao analisar o documento")

    return resultado
