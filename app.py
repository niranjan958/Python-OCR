import os
import re
import io
from flask import Flask, request, jsonify
import fitz
import pytesseract
from PIL import Image

app = Flask(__name__)

def extract_numbers(text):
    p1 = re.findall(r'\d{4}\s?\d{4}\s?\d{4}', text)
    p2 = re.findall(r'\d{12}', text)
    return list(set([n.replace(' ', '') for n in p1 + p2]))

def separate_numbers(nums):
    aadhaar, phones = [], []
    for n in nums:
        if n.startswith('91') and len(n) == 12 and n[2] in '6789':
            phones.append(n)
        else:
            aadhaar.append(n)
    return aadhaar, phones

def ocr_pdf(file_bytes):
    doc  = fitz.open(stream=file_bytes, filetype="pdf")
    page = doc[0]
    pix  = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img  = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    doc.close()
    w, h = img.size

    # Bottom half first (Aadhaar number always here)
    bottom = img.crop((0, int(h * 0.45), w, h))
    nums   = extract_numbers(pytesseract.image_to_string(bottom, config='--psm 6 --oem 1'))
    if nums:
        return nums

    # Left bottom (card front side)
    left = img.crop((0, int(h * 0.45), int(w * 0.5), h))
    nums = extract_numbers(pytesseract.image_to_string(left, config='--psm 6 --oem 1'))
    if nums:
        return nums

    # Full image last resort
    return extract_numbers(pytesseract.image_to_string(img, config='--psm 6 --oem 1'))

def ocr_image(file_bytes):
    img  = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size

    bottom = img.crop((0, int(h * 0.45), w, h))
    nums   = extract_numbers(pytesseract.image_to_string(bottom, config='--psm 6 --oem 1'))
    if nums:
        return nums

    return extract_numbers(pytesseract.image_to_string(img, config='--psm 6 --oem 1'))

@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR"})

@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        file = request.files.get('content') or request.files.get('file')
        if not file:
            return jsonify({"success": False, "numbers": [], "message": "No file"}), 400

        file_bytes = file.read()
        mime_type  = file.content_type or ''
        filename   = file.filename or ''

        print(f"[OCR] {filename} {len(file_bytes)} bytes")

        if 'pdf' in mime_type.lower() or filename.lower().endswith('.pdf'):
            all_numbers = ocr_pdf(file_bytes)
        else:
            all_numbers = ocr_image(file_bytes)

        aadhaar, phones = separate_numbers(all_numbers)

        print(f"[OCR] Found: {aadhaar}")

        return jsonify({
            "success":        True,
            "aadhaarNumbers": aadhaar,
            "phoneNumbers":   phones
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({"success": False, "aadhaarNumbers": [], "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
