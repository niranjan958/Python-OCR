from flask import Flask, request, jsonify
import pytesseract
from PIL import Image
import fitz  # PyMuPDF — already in requirements.txt
import io
import re
import os

app = Flask(__name__)

# ────────────────────────────────────────
# OCR HELPERS
# ────────────────────────────────────────

def ocr_image_bytes(image_bytes):
    """Run Tesseract on raw image bytes, return raw text."""
    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image, lang='eng', config='--psm 6')

def ocr_pdf_bytes(pdf_bytes):
    """
    Convert each PDF page to image using PyMuPDF (fitz),
    OCR each page, return combined raw text.
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    all_text = ''
    for page in doc:
        # Render page to image at 2x zoom for better OCR accuracy
        mat  = fitz.Matrix(2, 2)
        pix  = page.get_pixmap(matrix=mat)
        img  = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
        text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
        all_text += text + '\n'
    doc.close()
    return all_text

# ────────────────────────────────────────
# NUMBER EXTRACTION
# ────────────────────────────────────────

def find_aadhaar_numbers(text):
    """
    Extract valid-looking 12-digit numbers from text.
    Filters out phone numbers and numbers starting with 0.
    """
    spaced   = re.findall(r'\b\d{4}\s\d{4}\s\d{4}\b', text)
    spaced   = [n.replace(' ', '') for n in spaced]
    unspaced = re.findall(r'\b\d{12}\b', text)

    all_nums = list(dict.fromkeys(spaced + unspaced))  # deduplicate, keep order

    result = []
    for num in all_nums:
        if num.startswith('91') and num[2] in '6789':
            continue  # phone number
        if num.startswith('0'):
            continue
        result.append(num)
    return result

# ────────────────────────────────────────
# DIRECT TEXT SEARCH
# Search for entered number in raw text
# across multiple spacing/format variants
# ────────────────────────────────────────

def search_in_text(text, entered):
    if not text or not entered or len(entered) != 12:
        return False

    # No spaces: 733120405238
    if entered in re.sub(r'\s+', '', text):
        return True

    # Spaced groups of 4: 7331 2040 5238
    spaced = f"{entered[:4]} {entered[4:8]} {entered[8:12]}"
    if spaced in text:
        return True

    # Dashed: 7331-2040-5238
    dashed = f"{entered[:4]}-{entered[4:8]}-{entered[8:12]}"
    if dashed in text:
        return True

    return False

# ────────────────────────────────────────
# MAIN ROUTE
# ────────────────────────────────────────

@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        if 'content' not in request.files:
            return jsonify({'error': 'No file uploaded', 'aadhaarNumbers': [], 'rawText': '', 'enteredFound': False}), 400

        file          = request.files['content']
        file_bytes    = file.read()
        filename      = (file.filename or '').lower()
        entered       = request.form.get('entered_number', '').strip()

        # ── OCR ──────────────────────────────────────
        if filename.endswith('.pdf'):
            raw_text = ocr_pdf_bytes(file_bytes)
        else:
            raw_text = ocr_image_bytes(file_bytes)

        print(f"[OCR] snippet: {raw_text[:150]!r}")

        # ── Extract numbers ───────────────────────────
        aadhaar_numbers = find_aadhaar_numbers(raw_text)
        print(f"[OCR] extracted: {aadhaar_numbers}")

        # ── Direct search for entered number ──────────
        entered_found = False
        if entered and len(entered) == 12:
            entered_found = search_in_text(raw_text, entered)
            print(f"[OCR] entered={entered} found={entered_found}")

        return jsonify({
            'aadhaarNumbers': aadhaar_numbers,
            'rawText':        raw_text,       # Catalyst also searches this
            'enteredFound':   entered_found,  # direct answer: is it there?
            'success':        True
        })

    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({
            'error':          str(e),
            'aadhaarNumbers': [],
            'rawText':        '',
            'enteredFound':   False
        }), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
