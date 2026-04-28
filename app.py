import os
import re
import io
from flask import Flask, request, jsonify
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

app = Flask(__name__)

def extract_numbers_from_image(img):
    all_numbers = []
    w, h = img.size

    # Full image
    text = pytesseract.image_to_string(img, config='--psm 6')
    numbers = re.findall(r'\d{4}\s?\d{4}\s?\d{4}|\d{12}', text)
    all_numbers.extend([n.replace(' ', '') for n in numbers])

    # Bottom 60%
    bottom = img.crop((0, int(h * 0.4), w, h))
    text2 = pytesseract.image_to_string(bottom, config='--psm 6')
    numbers2 = re.findall(r'\d{4}\s?\d{4}\s?\d{4}|\d{12}', text2)
    all_numbers.extend([n.replace(' ', '') for n in numbers2])

    # Left half of bottom
    left_bottom = img.crop((0, int(h * 0.4), int(w * 0.5), h))
    text3 = pytesseract.image_to_string(left_bottom, config='--psm 6')
    numbers3 = re.findall(r'\d{4}\s?\d{4}\s?\d{4}|\d{12}', text3)
    all_numbers.extend([n.replace(' ', '') for n in numbers3])

    return list(set(all_numbers))


def separate_numbers(all_numbers):
    aadhaar_numbers = []
    phone_numbers   = []
    for num in all_numbers:
        if num.startswith('91') and len(num) == 12 and num[2] in '6789':
            phone_numbers.append(num)
        else:
            aadhaar_numbers.append(num)
    return aadhaar_numbers, phone_numbers


def process_pdf(pdf_bytes):
    all_numbers = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat  = fitz.Matrix(4, 4)
        pix  = page.get_pixmap(matrix=mat)
        img  = Image.open(io.BytesIO(pix.tobytes("png")))
        all_numbers.extend(extract_numbers_from_image(img))
    doc.close()
    return list(set(all_numbers))


def process_image(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    return list(set(extract_numbers_from_image(img)))


@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "service": "Aadhaar OCR Fallback",
        "engine": "Tesseract + PyMuPDF"
    })


@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        entered_number = re.sub(r'\D', '', request.args.get('aadhaar_number', ''))

        if len(entered_number) != 12:
            return jsonify({"success": False, "message": "Invalid aadhaar_number"}), 400

        file = request.files.get('content') or request.files.get('file')
        if not file:
            return jsonify({"success": False, "message": "No file uploaded"}), 400

        file_bytes = file.read()
        mime_type  = file.content_type or ''
        filename   = file.filename or ''

        print(f"[OCR] File: {filename}, Size: {len(file_bytes)}, MIME: {mime_type}")

        if 'pdf' in mime_type.lower() or filename.lower().endswith('.pdf'):
            all_numbers = process_pdf(file_bytes)
        else:
            all_numbers = process_image(file_bytes)

        aadhaar_numbers, phone_numbers = separate_numbers(all_numbers)

        detected_aadhaar = aadhaar_numbers[0] if aadhaar_numbers else ''
        match            = entered_number in aadhaar_numbers
        confidence       = 85 if match else (70 if aadhaar_numbers else 0)

        print(f"[OCR] Entered: {entered_number}, Detected: {aadhaar_numbers}, Match: {match}")

        message = (
            'Aadhaar Verified ✓' if match
            else 'Could not detect Aadhaar number' if not aadhaar_numbers
            else 'The number on the document does not match.'
        )

        return jsonify({
            "success": True,
            "match":   match,
            "data": {
                "entered":         entered_number,
                "detectedAadhaar": detected_aadhaar,
                "allNumbers":      aadhaar_numbers,
                "phoneNumbers":    phone_numbers,
                "confidence":      confidence,
                "engine":          "tesseract"
            },
            "message": message
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
