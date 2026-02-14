"""
Main FastAPI application with WhatsApp webhook endpoint
"""
from fastapi import FastAPI, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import Response, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import json

from app.utils.logger import app_logger
from app.utils.config import settings
from app.utils.image_processor import optimize_image_for_ocr, validate_image_format
from app.models import WhatsAppMessage, ProcessingResult, Invoice
from app.whatsapp_handler import whatsapp_handler
from app.ocr_processor import ocr_processor
from app.ncf_parser import ncf_parser
from app.export_handler import export_handler
from app.firebase_handler import firebase_handler

# Create FastAPI app
app = FastAPI(
    title="LECTOR-NCF",
    description="Sistema de Lectura OCR de Facturas NCF desde WhatsApp",
    version="1.0.0"
)

# Constants for Credentials
CREDENTIALS_DIR = Path("credentials")
FIREBASE_CRED_PATH = CREDENTIALS_DIR / "firebase-credentials.json"


def check_firebase_credentials():
    """Returns True if the Firebase credentials file exists."""
    return FIREBASE_CRED_PATH.exists()


@app.on_event("startup")
async def startup_event():
    """Application startup"""
    app_logger.info("=" * 50)
    app_logger.info("LECTOR-NCF Starting...")
    app_logger.info(f"Debug mode: {settings.debug}")
    app_logger.info("=" * 50)
    
    # Create necessary directories
    os.makedirs("data/temp", exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("data/exports", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    
    if not check_firebase_credentials():
        app_logger.warning("⚠️ FIREBASE CREDENTIALS NOT FOUND. Please visit http://localhost:8000/setup to configure them.")


@app.get("/")
async def root():
    """Root endpoint - redirects to setup if credentials are missing"""
    if not check_firebase_credentials():
        return RedirectResponse(url="/setup")
        
    return {
        "status": "running",
        "service": "LECTOR-NCF",
        "firebase_configured": True,
        "endpoints": {
            "webhook": "/webhook/whatsapp",
            "setup": "/setup"
        }
    }


# ==========================================
# SETUP UI (WEB CREDENTIAL SELECTOR)
# ==========================================

@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    """Displays the web UI to upload Firebase credentials"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>LECTOR-NCF | Configuración</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f4f7f6; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .container { background-color: white; padding: 40px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 100%; max-width: 500px; text-align: center; }
            h2 { color: #333; }
            .upload-box { border: 2px dashed #4CAF50; padding: 30px; margin: 20px 0; border-radius: 5px; }
            .btn { background-color: #4CAF50; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; width: 100%; margin-top: 10px;}
            .btn:hover { background-color: #45a049; }
            .success { color: green; font-weight: bold; margin-top: 15px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>🔥 Configuración de Firebase</h2>
            <p>El sistema requiere el archivo <b>firebase-credentials.json</b> para funcionar.</p>
            
            <form action="/setup/upload" method="post" enctype="multipart/form-data">
                <div class="upload-box">
                    <input type="file" name="file" accept=".json" required>
                </div>
                <input type="text" name="database_url" placeholder="URL de la Base de Datos (ej. https://...firebaseio.com)" required style="width: 100%; padding: 10px; margin-bottom: 15px; box-sizing: border-box;">
                <button type="submit" class="btn">💾 Guardar y Conectar</button>
            </form>
            
            """ + (f"<div class='success'>✅ Credenciales configuradas. El servidor está listo.</div>" if check_firebase_credentials() else "") + """
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/setup/upload")
async def upload_credentials(file: UploadFile = File(...), database_url: str = Form(...)):
    """Handles the upload of the credentials file and saves it"""
    try:
        if not file.filename.endswith('.json'):
            raise HTTPException(status_code=400, detail="El archivo debe ser un JSON")

        # Create credentials directory if it doesn't exist
        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        
        # Save the uploaded file
        with open(FIREBASE_CRED_PATH, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Verify it's a valid JSON
        try:
            with open(FIREBASE_CRED_PATH, "r") as f:
                json.load(f)
        except json.JSONDecodeError:
            os.remove(FIREBASE_CRED_PATH)
            raise HTTPException(status_code=400, detail="El archivo JSON es inválido o está corrupto.")

        # Re-initialize Firebase Handler
        settings.firebase_credentials = str(FIREBASE_CRED_PATH)
        settings.firebase_database_url = database_url
        firebase_handler.__init__()

        return RedirectResponse(url="/setup", status_code=303)
        
    except Exception as e:
        app_logger.error(f"Error saving credentials: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# WHATSAPP WEBHOOK
# ==========================================

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    MessageSid: str = Form(...),
    NumMedia: str = Form("0"),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
    Body: Optional[str] = Form(None)
):
    """WhatsApp webhook endpoint for receiving messages from Twilio"""
    
    # Check Firebase credentials before processing
    if not check_firebase_credentials():
        app_logger.error("Message received but Firebase is not configured.")
        return Response(content="", status_code=200)

    app_logger.info(f"Received WhatsApp message from {From}")
    
    try:
        num_media = int(NumMedia)
        
        if num_media == 0:
            whatsapp_handler.send_message(From, "Por favor envía una foto de la factura. 📸")
            return Response(content="", status_code=200)
        
        whatsapp_handler.send_confirmation(From)
        
        image_bytes = await whatsapp_handler.download_media(MediaUrl0, settings.twilio_auth_token)
        if not image_bytes:
            whatsapp_handler.send_error(From, "No se pudo descargar la imagen")
            return Response(content="", status_code=200)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        image_filename = f"factura_{timestamp}.jpg"
        temp_path = Path("data/temp") / image_filename
        
        with open(temp_path, 'wb') as f:
            f.write(image_bytes)
        
        optimized_image = optimize_image_for_ocr(image_bytes)
        ocr_text, confidence = ocr_processor.process_invoice_image(optimized_image)
        
        if not ocr_text:
            whatsapp_handler.send_error(From, "No se pudo leer texto en la imagen")
            return Response(content="", status_code=200)
        
        invoice = ncf_parser.parse_invoice(ocr_text, confidence, image_filename)
        
        warnings = []
        if not invoice.ncf: warnings.append("NCF no encontrado")
        if not invoice.montos.total: warnings.append("Monto total no encontrado")
        
        # Export and Save
        export_handler.export([invoice])
        
        try:
            firebase_handler.save_invoice(invoice)
        except Exception as e:
            app_logger.error(f"Firebase save failed: {e}")
        
        processed_path = Path("data/processed") / image_filename
        temp_path.rename(processed_path)
        
        if warnings:
            whatsapp_handler.send_partial_success(From, warnings)
        elif invoice.ncf:
            whatsapp_handler.send_success(From, invoice.ncf, invoice.montos.total)
        else:
            whatsapp_handler.send_error(From)
        
        return Response(content="", status_code=200)
        
    except Exception as e:
        app_logger.error(f"Error processing message: {e}")
        try:
            whatsapp_handler.send_error(From, "Error interno del sistema")
        except:
            pass
        return Response(content="", status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)