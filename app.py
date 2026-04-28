import os
import re
import io
import numpy as np
from flask import Flask, request, jsonify
import fitz
import pytesseract
from PIL import Image, ImageChops
import cv2
import piexif

app = Flask(__name__)

# ── Verhoeff ──────────────────────────────────────────
VERHOEFF_D = [
    [0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],
    [2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
    [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
    [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
    [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]
]
VERHOEFF_P = [
    [0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],
    [5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
    [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
    [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]
]

def verhoeff_validate(number):
    try:
        digits = [int(d) for d in reversed(str(number))]
        c = 0
        for i, d in enumerate(digits):
            c = VERHOEFF_D[c][VERHOEFF_P[i % 8][d]]
        return c == 0
    except:
        return False

# ── Helpers ───────────────────────────────────────────
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

def file_to_pil(file_bytes, mime_type, filename):
    if 'pdf' in mime_type.lower() or filename.lower().endswith('.pdf'):
        doc  = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc[0]
        pix  = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img  = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        doc.close()
        return img
    return Image.open(io.BytesIO(file_bytes)).convert("RGB")

def run_ocr(img):
    w, h = img.size
    all_nums = []

    bottom = img.crop((0, int(h * 0.45), w, h))
    t1 = pytesseract.image_to_string(bottom, config='--psm 6 --oem 1')
    all_nums.extend(extract_numbers(t1))

    if not all_nums:
        left_bottom = img.crop((0, int(h * 0.45), int(w * 0.5), h))
        t2 = pytesseract.image_to_string(left_bottom, config='--psm 6 --oem 1')
        all_nums.extend(extract_numbers(t2))

    if not all_nums:
        t3 = pytesseract.image_to_string(img, config='--psm 6 --oem 1')
        all_nums.extend(extract_numbers(t3))

    return separate_numbers(list(set(all_nums)))

# ── Forensic Checks ───────────────────────────────────
def check_file_signature(file_bytes, ext):
    header = file_bytes[:4].hex()
    sigs = {
        'ffd8ffe0': 'jpg', 'ffd8ffe1': 'jpg', 'ffd8ffdb': 'jpg',
        'ffd8ffe2': 'jpg', 'ffd8ffe3': 'jpg', 'ffd8ffee': 'jpg',
        '89504e47': 'png', '25504446': 'pdf'
    }
    detected = sigs.get(header, 'unknown')
    claimed  = ext.lower().replace('jpeg', 'jpg')
    return detected == claimed, detected, claimed

def check_exif(file_bytes):
    try:
        exif = piexif.load(file_bytes)
        software = exif.get('0th', {}).get(piexif.ImageIFD.Software, b'')
        if isinstance(software, bytes):
            software = software.decode('utf-8', errors='ignore').strip()
        editing_tools = ['photoshop','gimp','illustrator','inkscape','paint.net','pixlr','canva','corel']
        for tool in editing_tools:
            if tool in software.lower():
                return False, f"Edited with {software}"
        return True, software or 'None'
    except:
        return True, 'No EXIF'

def check_pdf_meta(file_bytes):
    try:
        doc  = fitz.open(stream=file_bytes, filetype="pdf")
        meta = doc.metadata
        doc.close()
        combined = (meta.get('producer','') + meta.get('creator','')).lower()
        editing_tools = ['photoshop','gimp','illustrator','inkscape','paint.net','pixlr','canva']
        for tool in editing_tools:
            if tool in combined:
                return False, f"PDF edited with {tool}"
        return True, meta.get('producer','') or meta.get('creator','') or 'Unknown'
    except:
        return True, 'No metadata'

def check_ela(img):
    try:
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        buf.seek(0)
        recomp = Image.open(buf).convert('RGB')
        ela    = ImageChops.difference(img, recomp)
        std    = float(np.array(ela, dtype=np.float32).std())
        return std < 12, round(std, 2)
    except:
        return True, 0

def check_noise(img):
    try:
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray   = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        h, w   = gray.shape
        blur   = cv2.GaussianBlur(gray, (5,5), 0)
        noise  = cv2.subtract(gray, blur).astype(np.float32)
        grid   = 4
        rh, rw = h // grid, w // grid
        vars_  = [float(np.var(noise[i*rh:(i+1)*rh, j*rw:(j+1)*rw]))
                  for i in range(grid) for j in range(grid)]
        std = float(np.std(vars_))
        return std < 30, round(std, 2)
    except:
        return True, 0

# ── Routes ────────────────────────────────────────────
@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar Forensic v3"})

@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        entered = re.sub(r'\D', '', request.args.get('aadhaar_number', ''))
        if len(entered) != 12:
            return jsonify({"success": False, "message": "Invalid number"}), 400

        file = request.files.get('content') or request.files.get('file')
        if not file:
            return jsonify({"success": False, "message": "No file"}), 400

        img          = file_to_pil(file.read(), file.content_type or '', file.filename or '')
        aadhaar, _   = run_ocr(img)
        detected     = aadhaar[0] if aadhaar else ''
        match        = entered in aadhaar

        return jsonify({
            "success": True,
            "match":   match,
            "data": {
                "entered":         entered,
                "detectedAadhaar": detected,
                "allNumbers":      aadhaar,
                "confidence":      85 if match else (70 if aadhaar else 0)
            },
            "message": 'Verified' if match else ('Not detected' if not aadhaar else 'Mismatch')
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/forensic', methods=['POST'])
def forensic():
    try:
        entered = re.sub(r'\D', '', request.args.get('aadhaar_number', ''))
        if len(entered) != 12:
            return jsonify({"success": False, "message": "Invalid number"}), 400

        file = request.files.get('content') or request.files.get('file')
        if not file:
            return jsonify({"success": False, "message": "No file"}), 400

        file_bytes = file.read()
        mime_type  = file.content_type or ''
        filename   = file.filename or ''
        ext        = filename.lower().split('.')[-1] if '.' in filename else ''
        is_pdf     = ext == 'pdf'

        if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
            return jsonify({"success": False, "message": f"Unsupported: {ext}"}), 400

        img         = file_to_pil(file_bytes, mime_type, filename)
        fraud_score = 100
        flags       = []

        # ── Check 1: File Signature ───────────────────
        sig_ok, detected_type, claimed_type = check_file_signature(file_bytes, ext)
        if not sig_ok:
            fraud_score -= 20
            flags.append(f"File type mismatch: claims {claimed_type} but is {detected_type}")

        # ── Check 2: Metadata ─────────────────────────
        if is_pdf:
            meta_ok, meta_info = check_pdf_meta(file_bytes)
        else:
            meta_ok, meta_info = check_exif(file_bytes)

        if not meta_ok:
            fraud_score -= 25
            flags.append(meta_info)

        # ── Check 3: OCR + Verhoeff ───────────────────
        aadhaar_nums, _ = run_ocr(img)
        number_on_card  = aadhaar_nums[0] if aadhaar_nums else ''
        number_match    = entered in aadhaar_nums if aadhaar_nums else False
        verhoeff_passed = None

        if number_on_card:
            verhoeff_passed = verhoeff_validate(number_on_card)
            if not verhoeff_passed:
                fraud_score -= 60
                flags.append(f"Number on card ({number_on_card}) fails Verhoeff checksum — TAMPERED")

        # ── Check 4: ELA (images only) ────────────────
        if not is_pdf:
            ela_ok, ela_std = check_ela(img)
            if not ela_ok:
                fraud_score -= 15
                flags.append(f"High ELA variance ({ela_std}) — possible pixel editing")

        # ── Check 5: Noise Consistency ────────────────
        noise_ok, noise_std = check_noise(img)
        if not noise_ok:
            fraud_score -= 15
            flags.append(f"Inconsistent noise pattern ({noise_std}) — possible tampering")

        # ── Final Verdict ─────────────────────────────
        fraud_score = max(0, min(100, fraud_score))

        if verhoeff_passed is False:
            forged      = True
            trust_level = 'Low'
            verdict     = f"TAMPERED — Number on card fails checksum"
        elif fraud_score >= 75:
            forged      = False
            trust_level = 'High'
            verdict     = 'GENUINE — No tampering detected'
        elif fraud_score >= 50:
            forged      = False
            trust_level = 'Medium'
            verdict     = 'SUSPICIOUS — Manual review recommended'
        else:
            forged      = True
            trust_level = 'Low'
            verdict     = 'FORGED — Multiple fraud signals detected'

        print(f"[FORENSIC] Score:{fraud_score} Forged:{forged} Number:{number_on_card} Match:{number_match}")

        return jsonify({
            "success": True,
            "numberCheck": {
                "enteredNumber": entered,
                "numberOnCard":  number_on_card or 'Not detected',
                "match":         number_match,
                "verdict": (
                    'Aadhaar number matches'               if number_match
                    else 'Could not detect number'         if not number_on_card
                    else 'Aadhaar number mismatch'
                )
            },
            "forensicAnalysis": {
                "forensicScore": fraud_score,
                "trustLevel":    trust_level,
                "forged":        forged,
                "verdict":       verdict,
                "totalFlags":    len(flags),
                "flags":         flags
            },
            "message": verdict
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
