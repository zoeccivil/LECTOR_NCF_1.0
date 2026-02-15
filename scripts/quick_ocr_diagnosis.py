"""
Diagnóstico rápido del OCR
Usa: python scripts/quick_ocr_diagnosis.py test_data/facturas_originales/factura_001.jpg
"""
import sys
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ocr_processor import ocr_processor
from app.utils.image_processor import optimize_image_for_ocr


def diagnose_ocr(image_path: str):
    """Diagnosticar qué está extrayendo el OCR"""
    
    print("="*70)
    print(f"🔍 DIAGNÓSTICO OCR: {Path(image_path).name}")
    print("="*70)
    
    # Leer y optimizar imagen
    with open(image_path, 'rb') as f:
        image_bytes = f.read()
    
    optimized = optimize_image_for_ocr(image_bytes)
    
    # OCR
    ocr_text, confidence = ocr_processor.process_invoice_image(optimized)
    
    print(f"\n✅ OCR Confidence: {confidence:.2%}")
    print(f"📝 Texto extraído: {len(ocr_text)} caracteres")
    
    # Buscar patrones
    print("\n" + "="*70)
    print("🔎 BUSCANDO PATRONES COMUNES")
    print("="*70)
    
    # NCF
    print("\n1️⃣ NCF:")
    ncf_patterns = [
        r'NCF[:\s]+([BE]\d{10})',
        r'([BE]\d{10})',
        r'([BE]\d{2}[- ]?\d{4}[- ]?\d{4})',
        r'N[°o]?\s*NCF[:\s]+([BE]\d+)',
    ]
    
    found_ncf = False
    for pattern in ncf_patterns:
        matches = re.findall(pattern, ocr_text, re.IGNORECASE)
        if matches:
            print(f"   ✅ Patrón '{pattern}' encontró: {matches}")
            found_ncf = True
    
    if not found_ncf:
        print("   ❌ No se encontró NCF con patrones conocidos")
        print("   📄 Buscando 'NCF' en el texto:")
        ncf_context = []
        for i, line in enumerate(ocr_text.split('\n')):
            if 'NCF' in line.upper() or re.search(r'[BE]\d{10}', line):
                ncf_context.append(f"      Línea {i}: {line.strip()}")
        if ncf_context:
            print("\n".join(ncf_context[:5]))
    
    # RNC
    print("\n2️⃣ RNC:")
    rnc_patterns = [
        r'RNC[:\s]+(\d{9,11})',
        r'RNC[:\s]*[:]?\s*(\d{9,11})',
        r'R\.N\.C\.?\s*[:]?\s*(\d{9,11})',
    ]
    
    found_rnc = False
    for pattern in rnc_patterns:
        matches = re.findall(pattern, ocr_text, re.IGNORECASE)
        if matches:
            print(f"   ✅ Patrón '{pattern}' encontró: {matches}")
            found_rnc = True
    
    if not found_rnc:
        print("   ❌ No se encontró RNC con patrones conocidos")
        print("   📄 Buscando 'RNC' en el texto:")
        for i, line in enumerate(ocr_text.split('\n')):
            if 'RNC' in line.upper():
                print(f"      Línea {i}: {line.strip()}")
    
    # TOTAL
    print("\n3️⃣ TOTAL:")
    total_patterns = [
        r'TOTAL[:\s]+(?:RD\$|RD|[$])\s*([\d,]+\.?\d*)',
        r'TOTAL[:\s]+([\d,]+\.?\d*)',
        r'(?:RD\$|RD|[$])\s*([\d,]+\.?\d*)\s*$',
    ]
    
    found_total = False
    for pattern in total_patterns:
        matches = re.findall(pattern, ocr_text, re.IGNORECASE | re.MULTILINE)
        if matches:
            print(f"   ✅ Patrón '{pattern}' encontró: {matches}")
            found_total = True
    
    if not found_total:
        print("   �� No se encontró TOTAL con patrones conocidos")
        print("   📄 Buscando 'TOTAL' en el texto:")
        for i, line in enumerate(ocr_text.split('\n')):
            if 'TOTAL' in line.upper():
                print(f"      Línea {i}: {line.strip()}")
    
    # Mostrar texto completo
    print("\n" + "="*70)
    print("📄 TEXTO COMPLETO DEL OCR")
    print("="*70)
    print(ocr_text)
    print("="*70)
    
    # Recomendaciones
    print("\n💡 RECOMENDACIONES:")
    print("─"*70)
    
    if not found_ncf:
        print("⚠️ NCF no detectado - Verifica que el NCF esté visible en la imagen")
        print("   • Revisa app/ncf_parser.py línea ~50 (_extract_ncf)")
        print("   • Agrega el patrón específico que ves en el texto OCR")
    
    if not found_rnc:
        print("⚠️ RNC no detectado - Verifica que el RNC esté visible")
        print("   • Revisa app/ncf_parser.py línea ~80 (_extract_rnc)")
    
    if not found_total:
        print("⚠️ TOTAL no detectado - Problema común")
        print("   • Revisa app/ncf_parser.py línea ~120 (_extract_amounts)")
        print("   • El parser busca 'TOTAL' seguido de monto")
        print("   • Verifica el formato exacto en el texto OCR arriba")
    
    print("="*70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("❌ Uso: python scripts/quick_ocr_diagnosis.py <imagen>")
        print("\nEjemplo:")
        print("  python scripts/quick_ocr_diagnosis.py test_data/facturas_originales/factura_001.jpg")
        sys.exit(1)
    
    diagnose_ocr(sys.argv[1])