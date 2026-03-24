import os
import tempfile
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from fastapi.security import APIKeyHeader
from dotenv import load_dotenv
from datetime import datetime
from pymongo import MongoClient
from zoneinfo import ZoneInfo
import time

from main import analyze_file

load_dotenv()

app = FastAPI()

MONGO_URI = os.getenv("MONGO_URI")
LIMITE_USO_DEFAULT = 1000

api_key_header = APIKeyHeader(name="Authorization")

def check_token(api_key: str = Depends(api_key_header)):

    with MongoClient(MONGO_URI) as client:
        db = client["SKILLFUL"]
        collection = db["MONITORAMENTO"]
        doc = collection.find_one({"TOKEN": api_key})
        if not doc:
            raise HTTPException(401, "Token invalido")

        if doc.get('LIMITE_USO', LIMITE_USO_DEFAULT) <= doc.get('USO', 0):
            raise HTTPException(403, "Limite de uso atingido. Fale com o administrador para renovar seu limite.")
        
    return api_key

@app.post("/analyze")
def analyze_invoice(
    documento: UploadFile = File(...),
    token: str = Depends(check_token)
) -> dict[str, Any]:

    if documento.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=400, detail="O documento precisa ser um PDF")

    start = time.time()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.join(temp_dir, documento.filename or "documento.pdf")
        with open(temp_path, "wb") as temp_file:
            temp_file.write(documento.file.read())

        resultado = analyze_file(temp_path)
    
    end = time.time()

    if not resultado:
        raise HTTPException(status_code=500, detail="Falha ao analisar o documento")

    custo = resultado.get('custo', None)

    if custo:
        with MongoClient(MONGO_URI) as client:
            db = client["SKILLFUL"]
            collection = db["MONITORAMENTO"]
            collection.update_one({"TOKEN": token}, {"$inc": {"CUSTO_TOTAL": custo, "USO": 1}})

            collection = db["HISTORICO"]
            collection.insert_one({
                "TOKEN": token,
                "DT_USO": datetime.now(ZoneInfo("America/Sao_Paulo")),
                "CUSTO": custo,
                "TEMPO_PROCESSAMENTO": end - start
            })

    return resultado
