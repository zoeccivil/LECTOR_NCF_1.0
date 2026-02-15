"""
Intelligent parser for extracting NCF invoice data from OCR text
"""
import re
from typing import Optional, Dict, Any
from datetime import datetime
from dateutil import parser as date_parser
from app.utils.validators import extract_ncf_from_text, extract_rnc_from_text, validate_amount_coherence
from app.utils.logger import app_logger
from app.models import Invoice, InvoiceAmounts, InvoiceMetadata


class NCFParser:
    """Intelligent parser for Dominican NCF invoices"""
    
    def __init__(self):
        """Initialize parser with patterns"""
        # Amount patterns (handles different formats)
        self.amount_patterns = [
            r'(?:RD\$|DOP|\$)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',  # 1,234.56
            r'(?:RD\$|DOP|\$)?\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)',  # 1.234,56
            r'(\d+\.?\d*)',  # Simple numbers
        ]
        
        # Date patterns
        self.date_patterns = [
            r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}',  # DD/MM/YYYY or MM/DD/YYYY
            r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',    # YYYY-MM-DD
        ]
        
        # Business name indicators
        self.business_indicators = [
            'razon social', 'razón social', 'nombre', 'empresa',
            'contribuyente', 'emisor', 'proveedor'
        ]
    
    def parse_invoice(self, ocr_text: str, confidence: Optional[float] = None,
                     image_filename: Optional[str] = None) -> Invoice:
        """
        Parse OCR text and extract invoice data
        
        Args:
            ocr_text: Text extracted from OCR
            confidence: OCR confidence score
            image_filename: Original image filename
            
        Returns:
            Invoice object with extracted data
        """
        app_logger.info("Parsing invoice data from OCR text")
        
        # Create invoice object
        invoice = Invoice()
        
        # Extract NCF
        invoice.ncf = self._extract_ncf(ocr_text)
        
        # Extract RNC
        invoice.rnc = self._extract_rnc(ocr_text)
        
        # Extract business name
        invoice.razon_social = self._extract_business_name(ocr_text)
        
        # Extract date
        invoice.fecha_emision = self._extract_date(ocr_text)
        
        # Extract amounts
        invoice.montos = self._extract_amounts(ocr_text)
        
        # Set metadata
        invoice.metadata = InvoiceMetadata(
            imagen_original=image_filename,
            confianza_ocr=confidence,
            origen="whatsapp"
        )
        
        # Store full text for debugging
        invoice.texto_completo = ocr_text
        
        # Log extraction results
        self._log_extraction_results(invoice)
        
        return invoice
    
    def _extract_ncf(self, text: str) -> Optional[str]:
        """Extract NCF from text"""
        ncf = extract_ncf_from_text(text)
        if ncf:
            app_logger.info(f"Extracted NCF: {ncf}")
        else:
            app_logger.warning("NCF not found in text")
        return ncf
    
    def _extract_rnc(self, text: str) -> Optional[str]:
        """
        Extract RNC (Registro Nacional de Contribuyentes)
        Formatos:
        - 101019921
        - 1-8311147-2
        - 133-387263
        """
        patterns = [
            r'RNC[:\s]+(\d{9,11})',                    # RNC: 101019921
            r'RNC[:\s]*[:]?\s*(\d{9,11})',            # RNC : 101019921
            r'R\.N\.C\.?\s*:?\s*(\d{9,11})',          # R.N.C: 101019921
            r'RNC[:\s]+(\d{1,3}-\d{7}-\d{1})',        # RNC: 1-8311147-2 ← NUEVO
            r'RNC[:\s]+(\d{3}-\d{6})',                # RNC: 133-387263 ← NUEVO
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                rnc = match.group(1).replace('-', '').replace(' ', '')
                # Validar que sea numérico y longitud correcta
                if rnc.isdigit() and 9 <= len(rnc) <= 11:
                    return rnc
        
        return None
    
    def _extract_business_name(self, text: str) -> Optional[str]:
        """Extract business/company name from text"""
        lines = text.split('\n')
        
        # Look for lines containing business indicators
        for i, line in enumerate(lines):
            line_lower = line.lower()
            for indicator in self.business_indicators:
                if indicator in line_lower:
                    # Business name might be on the same line or next line
                    if ':' in line:
                        name = line.split(':', 1)[1].strip()
                        if name and len(name) > 3:
                            app_logger.info(f"Extracted business name: {name}")
                            return name
                    elif i + 1 < len(lines):
                        name = lines[i + 1].strip()
                        if name and len(name) > 3:
                            app_logger.info(f"Extracted business name: {name}")
                            return name
        
        # Fallback: use first non-empty line (often company name is at top)
        for line in lines[:5]:  # Check first 5 lines
            line = line.strip()
            if len(line) > 5 and not any(char.isdigit() for char in line):
                app_logger.info(f"Extracted business name (fallback): {line}")
                return line
        
        app_logger.warning("Business name not found")
        return None
    
    def _extract_date(self, text: str) -> Optional[str]:
        """Extract invoice date from text"""
        # Look for date patterns
        for pattern in self.date_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                try:
                    # Try to parse the date
                    parsed_date = date_parser.parse(match, dayfirst=True)
                    date_str = parsed_date.strftime('%Y-%m-%d')
                    app_logger.info(f"Extracted date: {date_str}")
                    return date_str
                except:
                    continue
        
        app_logger.warning("Date not found in text")
        return None
    
    def _extract_amounts(self, text: str):
        """
        Extract subtotal, ITBIS, and total amounts
        Busca en múltiples formatos y líneas
        """
        lines = text.split('\n')
        
        # ESTRATEGIA 1: TOTAL en la misma línea
        total_patterns_inline = [
            r'TOTAL[:\s]+(?:RD\$|RD|[$])\s*([\d,\.]+)',      # TOTAL RD$ 1,234.56
            r'TOTAL[:\s]+([\d,\.]+)',                         # TOTAL 1,234.56
            r'TOTAL\s*:\s*(?:RD\$|RD|[$])?\s*([\d,\.]+)',    # TOTAL: RD$1,234.56
            r'(?:TOTAL\s+A\s+PAGAR)[:\s]+(?:RD\$|RD|[$])?\s*([\d,\.]+)', # TOTAL A PAGAR 1234
        ]
        
        for pattern in total_patterns_inline:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                total_str = match.group(1).replace(',', '').replace('.', '', total_str.count('.') - 1)
                try:
                    self.montos.total = float(total_str)
                    app_logger.info(f"Total encontrado (inline): {self.montos.total}")
                    break
                except ValueError:
                    continue
        
        # ESTRATEGIA 2: TOTAL en línea separada (buscar en las siguientes 3 líneas)
        if not self.montos.total:
            for i, line in enumerate(lines):
                if re.search(r'\bTOTAL\b', line, re.IGNORECASE):
                    # Buscar monto en las siguientes 3 líneas
                    for j in range(i, min(i + 4, len(lines))):
                        next_line = lines[j]
                        # Buscar patrón de monto
                        amount_patterns = [
                            r'(?:RD\$|RD|[$]|DOP)\s*([\d,\.]+)',
                            r'^\s*([\d,\.]+)\s*$',
                            r'Monto\s*\$?([\d,\.]+)',
                        ]
                        
                        for pattern in amount_patterns:
                            match = re.search(pattern, next_line)
                            if match:
                                total_str = match.group(1).replace(',', '')
                                # Manejar punto como separador de miles
                                if total_str.count('.') > 1:
                                    total_str = total_str.replace('.', '', total_str.count('.') - 1)
                                try:
                                    total = float(total_str)
                                    # Validar que sea un monto razonable (> 10)
                                    if total > 10:
                                        self.montos.total = total
                                        app_logger.info(f"Total encontrado (multiline): {self.montos.total} en línea {j}")
                                        break
                                except ValueError:
                                    continue
                        
                        if self.montos.total:
                            break
                    
                    if self.montos.total:
                        break
        
        # SUBTOTAL
        subtotal_patterns = [
            r'SUBTOTAL[:\s]+(?:RD\$|RD|[$])?\s*([\d,\.]+)',
            r'Sub\s*Total[:\s]+(?:RD\$|RD|[$])?\s*([\d,\.]+)',
        ]
        
        for pattern in subtotal_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                subtotal_str = match.group(1).replace(',', '')
                try:
                    self.montos.subtotal = float(subtotal_str)
                    break
                except ValueError:
                    continue
        
        # ITBIS
        itbis_patterns = [
            r'ITBIS[:\s]+(?:RD\$|RD|[$])?\s*([\d,\.]+)',
            r'(?:18|16)%?\s*ITBIS[:\s]+(?:RD\$|RD|[$])?\s*([\d,\.]+)',
        ]
        
        for pattern in itbis_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                itbis_str = match.group(1).replace(',', '')
                try:
                    self.montos.itbis = float(itbis_str)
                    break
                except ValueError:
                    continue
    
    def _find_amount_near_keyword(self, text: str, text_lower: str, keywords: list) -> Optional[float]:
        """Find amount near specific keywords using word boundary matching"""
        lines = text.split('\n')
        lines_lower = text_lower.split('\n')
        
        for keyword in keywords:
            for i, line_lower in enumerate(lines_lower):
                # Use word boundaries to ensure exact keyword match
                # This prevents 'total' from matching 'subtotal'
                pattern = r'\b' + re.escape(keyword) + r'\b'
                if re.search(pattern, line_lower):
                    # First, try to find amount on the same line
                    amount = self._extract_first_amount(lines[i])
                    if amount and amount > 0:
                        return amount
                    
                    # If not found, check next line
                    if i + 1 < len(lines):
                        amount = self._extract_first_amount(lines[i + 1])
                        if amount and amount > 0:
                            return amount
        
        return None
    
    def _extract_first_amount(self, text: str) -> Optional[float]:
        """Extract first valid amount from text"""
        # Remove currency symbols
        text = re.sub(r'RD\$|DOP|\$', '', text)
        
        # Try pattern: 1,234.56 (US format) - at least 2 decimal places
        match = re.search(r'(\d{1,3}(?:,\d{3})+\.\d{2})', text)
        if match:
            amount_str = match.group(1).replace(',', '')
            try:
                return float(amount_str)
            except:
                pass
        
        # Try pattern: 1234.56 (simple US format with decimals)
        match = re.search(r'\b(\d+\.\d{2})\b', text)
        if match:
            try:
                amount = float(match.group(1))
                # Only return if it looks like a reasonable amount (not part of an ID)
                if amount > 0 and amount < 1000000:
                    return amount
            except:
                pass
        
        # Try pattern: 1.234,56 (European format)
        match = re.search(r'(\d{1,3}(?:\.\d{3})+,\d{2})', text)
        if match:
            amount_str = match.group(1).replace('.', '').replace(',', '.')
            try:
                return float(amount_str)
            except:
                pass
        
        return None
    
    def _calculate_missing_amounts(self, amounts: InvoiceAmounts) -> InvoiceAmounts:
        """Calculate missing amounts if possible"""
        # If we have total and ITBIS, calculate subtotal
        if amounts.total and amounts.itbis and not amounts.subtotal:
            amounts.subtotal = round(amounts.total - amounts.itbis, 2)
            app_logger.info(f"Calculated subtotal: {amounts.subtotal}")
        
        # If we have subtotal and total, calculate ITBIS
        if amounts.subtotal and amounts.total and not amounts.itbis:
            amounts.itbis = round(amounts.total - amounts.subtotal, 2)
            app_logger.info(f"Calculated ITBIS: {amounts.itbis}")
        
        # If we have subtotal and ITBIS, calculate total
        if amounts.subtotal and amounts.itbis and not amounts.total:
            amounts.total = round(amounts.subtotal + amounts.itbis, 2)
            app_logger.info(f"Calculated total: {amounts.total}")
        
        return amounts
    
    def _log_extraction_results(self, invoice: Invoice):
        """Log extraction results summary"""
        app_logger.info("=== Invoice Extraction Summary ===")
        app_logger.info(f"NCF: {invoice.ncf or 'NOT FOUND'}")
        app_logger.info(f"RNC: {invoice.rnc or 'NOT FOUND'}")
        app_logger.info(f"Business: {invoice.razon_social or 'NOT FOUND'}")
        app_logger.info(f"Date: {invoice.fecha_emision or 'NOT FOUND'}")
        app_logger.info(f"Subtotal: {invoice.montos.subtotal or 'NOT FOUND'}")
        app_logger.info(f"ITBIS: {invoice.montos.itbis or 'NOT FOUND'}")
        app_logger.info(f"Total: {invoice.montos.total or 'NOT FOUND'}")
        app_logger.info("================================")


# Global parser instance
ncf_parser = NCFParser()
