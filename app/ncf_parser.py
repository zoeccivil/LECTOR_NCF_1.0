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
        """Extract RNC from text"""
        rnc = extract_rnc_from_text(text)
        if rnc:
            app_logger.info(f"Extracted RNC: {rnc}")
        else:
            app_logger.warning("RNC not found in text")
        return rnc
    
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
    
    def _extract_amounts(self, text: str) -> InvoiceAmounts:
        """Extract monetary amounts from text"""
        amounts = InvoiceAmounts()
        
        # Normalize text for easier parsing
        text_lower = text.lower()
        
        # Extract total
        total_value = self._find_amount_near_keyword(text, text_lower, ['total', 'monto total', 'total general'])
        if total_value:
            amounts.total = total_value
            app_logger.info(f"Extracted total: {total_value}")
        
        # Extract ITBIS
        itbis_value = self._find_amount_near_keyword(text, text_lower, ['itbis', 'itebis', 'impuesto', 'tax'])
        if itbis_value:
            amounts.itbis = itbis_value
            app_logger.info(f"Extracted ITBIS: {itbis_value}")
        
        # Extract subtotal
        subtotal_value = self._find_amount_near_keyword(text, text_lower, ['subtotal', 'sub total', 'sub-total', 'valor'])
        if subtotal_value:
            amounts.subtotal = subtotal_value
            app_logger.info(f"Extracted subtotal: {subtotal_value}")
        
        # Try to calculate missing values
        amounts = self._calculate_missing_amounts(amounts)
        
        # Validate coherence
        if amounts.subtotal and amounts.itbis and amounts.total:
            if not validate_amount_coherence(amounts.subtotal, amounts.itbis, amounts.total):
                app_logger.warning("Amount coherence validation failed - values may be incorrect")
        
        return amounts
    
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
