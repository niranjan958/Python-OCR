from flask import Flask, request, jsonify
import pytesseract
from PIL import Image
import io
import re
import pdf2image
import os
import tempfile

app = Flask(__name__)

def extract_text_from_image(image_bytes):
    """Run Tesseract OCR on image bytes, return raw text."""
    image = Image.open(io.BytesIO(image_bytes))
    # Use both standard and Aadhaar-optimized config
    text = pytesseract.image_to_string(image, lang='eng', config='--psm 6')
    return text

def extract_text_from_pdf(pdf_bytes):
    """Convert PDF pages to images, OCR each, return combined raw text."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        pages = pdf2image.convert_from_path(tmp_path, dpi=200)
        all_text = ''
        for page in pages:
            buf = io.BytesIO()
            page.save(buf, format='PNG')
            text = pytesseract.image_to_string(
                Image.open(io.BytesIO(buf.getvalue())),
                lang='eng',
                config='--psm 6'
            )
            all_text += text + '\n'
        return all_text
    finally:
        os.unlink(tmp_path)

def find_aadhaar_numbers(text):
    """
    Extract valid-looking 12-digit Aadhaar numbers from text.
    Filters out phone numbers and other IDs.
    """
    # Match spaced format: 7331 2040 5238
    spaced = re.findall(r'\b\d{4}\s\d{4}\s\d{4}\b', text)
    spaced = [n.replace(' ', '') for n in spaced]

    # Match unspaced: 733120405238
    unspaced = re.findall(r'\b\d{12}\b', text)

    all_nums = list(dict.fromkeys(spaced + unspaced))  # deduplicate, preserve order

    # Filter phone numbers (starts with 91 + mobile digit 6-9)
    result = []
    for num in all_nums:
        if num.startswith('91') and num[2] in '6789':
            continue
        if num.startswith('0'):
            continue
        result.append(num)

    return result

def search_in_text(text, entered):
    """
    Directly search for entered number in raw OCR text.
    Checks multiple formats: no-space, spaced, dashed.
    """
    if not text or not entered or len(entered) != 12:
        return False

    # No-space search
    no_space = re.sub(r'\s+', '', text)
    if entered in no_space:
        return True

    # Spaced: 7331 2040 5238
    spaced = f"{entered[:4]} {entered[4:8]} {entered[8:12]}"
    if spaced in text:
        return True

    # Dashed: 7331-2040-5238
    dashed = f"{entered[:4]}-{entered[4:8]}-{entered[8:12]}"
    if dashed in text:
        return True

    return False

@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        if 'content' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file        = request.files['content']
        file_bytes  = file.read()
        filename    = file.filename.lower()
        entered     = request.form.get('entered_number', '').strip()  # NEW

        # OCR
        if filename.endswith('.pdf'):
            raw_text = extract_text_from_pdf(file_bytes)
        else:
            raw_text = extract_text_from_image(file_bytes)

        print(f"[OCR] raw text snippet: {raw_text[:150]}")

        # Extract Aadhaar numbers
        aadhaar_numbers = find_aadhaar_numbers(raw_text)
        print(f"[OCR] extracted numbers: {aadhaar_numbers}")

        # Direct search for entered number  ← NEW
        entered_found = False
        if entered and len(entered) == 12:
            entered_found = search_in_text(raw_text, entered)
            print(f"[OCR] entered={entered} found={entered_found}")

        return jsonify({
            'aadhaarNumbers': aadhaar_numbers,
            'rawText':        raw_text,        # NEW — Catalyst searches this too
            'enteredFound':   entered_found,   # NEW — direct answer to "is it there?"
            'success':        True
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({'error': str(e), 'aadhaarNumbers': [], 'rawText': '', 'enteredFound': False}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
