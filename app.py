import os
import re
import io
import json
import struct
import numpy as np
from flask import Flask, request, jsonify
import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageChops
import cv2
import ExifRead
 
app = Flask(__name__)
 
# ════════════════════════════════════════════════════════
# VERHOEFF ALGORITHM
# ════════════════════════════════════════════════════════
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
 
# ════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════
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
 
    # Bottom half first (Aadhaar number always here)
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
 
# ════════════════════════════════════════════════════════
# FORENSIC CHECKS
# ════════════════════════════════════════════════════════
 
def check_file_signature(file_bytes, ext):
    """Verify file type matches claimed extension"""
    header = file_bytes[:4].hex()
    sigs = {
        'ffd8ffe0': 'jpg', 'ffd8ffe1': 'jpg', 'ffd8ffdb': 'jpg',
        'ffd8ffe2': 'jpg', 'ffd8ffe3': 'jpg', 'ffd8ffee': 'jpg',
        '89504e47': 'png', '25504446': 'pdf'
    }
    detected = sigs.get(header, 'unknown')
    claimed  = ext.lower().replace('jpeg', 'jpg')
    return {
        'claimed':  claimed,
        'detected': detected,
        'header':   header,
        'ok':       detected == claimed
    }
 
def check_exif_metadata(file_bytes, filename):
    """Check EXIF for editing software signatures"""
    flags    = []
    metadata = {}
    
    try:
        import piexif
        exif_dict = piexif.load(file_bytes)
        
        # Check Software tag (0x0131)
        software = exif_dict.get('0th', {}).get(piexif.ImageIFD.Software, b'')
        if isinstance(software, bytes):
            software = software.decode('utf-8', errors='ignore').strip()
        
        editing_tools = [
            'photoshop', 'gimp', 'illustrator', 'inkscape',
            'paint.net', 'pixlr', 'canva', 'corel', 'affinity'
        ]
        
        if software:
            metadata['software'] = software
            for tool in editing_tools:
                if tool in software.lower():
                    flags.append(f"❌ Image edited with {software}")
                    break
        
        # Check DateTime vs DateTimeOriginal
        dt_orig = exif_dict.get('Exif', {}).get(piexif.ExifIFD.DateTimeOriginal, b'')
        dt_mod  = exif_dict.get('0th', {}).get(piexif.ImageIFD.DateTime, b'')
        
        if dt_orig and dt_mod:
            dt_orig = dt_orig.decode('utf-8', errors='ignore') if isinstance(dt_orig, bytes) else str(dt_orig)
            dt_mod  = dt_mod.decode('utf-8', errors='ignore') if isinstance(dt_mod, bytes) else str(dt_mod)
            metadata['created']  = dt_orig
            metadata['modified'] = dt_mod
            if dt_orig != dt_mod:
                flags.append("⚠️ File was modified after original creation")
                
    except Exception as e:
        metadata['note'] = f'EXIF not available ({str(e)[:50]})'
    
    return flags, metadata
 
def check_ela(img, quality=85):
    """Error Level Analysis - detect recompressed/edited regions"""
    try:
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        buf.seek(0)
        recomp    = Image.open(buf).convert('RGB')
        ela       = ImageChops.difference(img, recomp)
        ela_array = np.array(ela, dtype=np.float32)
        
        std  = float(ela_array.std())
        mean = float(ela_array.mean())
        
        return {
            'mean':       round(mean, 3),
            'std':        round(std, 3),
            'suspicious': std > 12
        }
    except:
        return {'error': 'ELA failed', 'suspicious': False}
 
def check_noise_consistency(img):
    """
    Check if noise pattern is consistent across the image.
    Edited areas have different noise characteristics.
    """
    try:
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray   = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        h, w   = gray.shape
 
        blur  = cv2.GaussianBlur(gray, (5, 5), 0)
        noise = cv2.subtract(gray, blur).astype(np.float32)
 
        grid  = 4
        rh, rw = h // grid, w // grid
        variances = []
        for i in range(grid):
            for j in range(grid):
                region = noise[i*rh:(i+1)*rh, j*rw:(j+1)*rw]
                variances.append(float(np.var(region)))
 
        noise_std = float(np.std(variances))
 
        return {
            'noise_std':   round(noise_std, 2),
            'suspicious':  noise_std > 30
        }
    except:
        return {'error': 'Noise analysis failed', 'suspicious': False}
 
def check_copy_move(img):
    """Detect copy-move forgery - identical blocks in different locations"""
    try:
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray   = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        h, w   = gray.shape
 
        block_size = 16
        step       = 32
        blocks     = {}
        duplicates = 0
 
        for y in range(0, h - block_size, step):
            for x in range(0, w - block_size, step):
                block = gray[y:y+block_size, x:x+block_size]
                key   = block.tobytes()
                if key in blocks:
                    duplicates += 1
                else:
                    blocks[key] = (x, y)
 
        # Too many duplicates in a genuine card is suspicious
        total_blocks = len(blocks) + duplicates
        dup_ratio    = duplicates / total_blocks if total_blocks > 0 else 0
 
        return {
            'duplicate_blocks': duplicates,
            'ratio':            round(dup_ratio, 3),
            'suspicious':       dup_ratio > 0.15 and duplicates > 50
        }
    except:
        return {'error': 'Copy-move check failed', 'suspicious': False}
 
def check_pdf_metadata(file_bytes):
    """Check PDF metadata for editing tools"""
    flags    = []
    metadata = {}
    try:
        doc  = fitz.open(stream=file_bytes, filetype="pdf")
        meta = doc.metadata
        doc.close()
 
        producer = (meta.get('producer', '') or '').lower()
        creator  = (meta.get('creator', '')  or '').lower()
        combined = producer + ' ' + creator
 
        metadata = {
            'producer':     meta.get('producer', 'Unknown'),
            'creator':      meta.get('creator',  'Unknown'),
            'created':      meta.get('creationDate', 'Unknown'),
            'modified':     meta.get('modDate',      'Unknown'),
        }
 
        editing_tools = ['photoshop', 'gimp', 'illustrator', 'inkscape', 'paint.net', 'pixlr', 'canva']
        for tool in editing_tools:
            if tool in combined:
                flags.append(f"❌ PDF created/edited with {tool}")
                break
 
        created  = meta.get('creationDate', '')
        modified = meta.get('modDate', '')
        if created and modified and created != modified:
            flags.append("⚠️ PDF was modified after original creation")
 
    except Exception as e:
        metadata['note'] = f'PDF metadata not available: {str(e)[:50]}'
 
    return flags, metadata
 
# ════════════════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════════════════
 
@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "status":  "ok",
        "service": "Aadhaar OCR + Forensic (Python Only)",
        "version": "3.0"
    })
 
 
@app.route('/ocr', methods=['POST'])
def ocr():
    """OCR only endpoint"""
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
 
        img = file_to_pil(file_bytes, mime_type, filename)
        aadhaar_nums, phone_nums = run_ocr(img)
 
        detected = aadhaar_nums[0] if aadhaar_nums else ''
        match    = entered_number in aadhaar_nums
 
        return jsonify({
            "success": True,
            "match":   match,
            "data": {
                "entered":         entered_number,
                "detectedAadhaar": detected,
                "allNumbers":      aadhaar_nums,
                "phoneNumbers":    phone_nums,
                "confidence":      85 if match else (70 if aadhaar_nums else 0),
                "engine":          "tesseract"
            },
            "message": (
                'Aadhaar Verified'             if match
                else 'Could not detect number' if not aadhaar_nums
                else 'Number does not match'
            )
        })
 
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
 
 
@app.route('/forensic', methods=['POST'])
def forensic():
    """
    Full forensic analysis (Python only, no AI):
    1. OCR + Verhoeff → number tampered?
    2. EXIF metadata → editing software detected?
    3. ELA → pixel-level edits?
    4. Noise consistency → regions with different noise?
    5. Copy-move detection → copy-pasted areas?
    6. File signature → file type spoofing?
    """
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
        ext        = filename.lower().split('.')[-1] if '.' in filename else ''
 
        # Validate file type
        if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
            return jsonify({
                "success": False,
                "message": f"Unsupported file: {ext}. Allowed: pdf, jpg, jpeg, png"
            }), 400
 
        print(f"[FORENSIC] File: {filename}, Size: {len(file_bytes)}, Entered: {entered_number}")
 
        img           = file_to_pil(file_bytes, mime_type, filename)
        fraud_score   = 100
        flags         = []
        details       = {}
        is_pdf        = ext == 'pdf'
 
        # ── CHECK 1: File Signature ───────────────────
        sig = check_file_signature(file_bytes, ext)
        details['fileSignature'] = sig
        if not sig['ok']:
            fraud_score -= 20
            flags.append(f"⚠️ [FILE] Type mismatch — claims {sig['claimed']} but is {sig['detected']}")
 
        # ── CHECK 2: Metadata (EXIF for images, PDF meta for PDFs) ──
        if is_pdf:
            meta_flags, meta_data = check_pdf_metadata(file_bytes)
        else:
            meta_flags, meta_data = check_exif_metadata(file_bytes, filename)
 
        details['metadata'] = meta_data
        for f in meta_flags:
            flags.append(f)
            fraud_score -= (25 if '❌' in f else 10)
 
        # ── CHECK 3: OCR → extract number from card ───
        aadhaar_nums, phone_nums = run_ocr(img)
        number_on_card = aadhaar_nums[0] if aadhaar_nums else ''
        number_match   = entered_number in aadhaar_nums if aadhaar_nums else False
 
        print(f"[FORENSIC] OCR found: {aadhaar_nums}, Match: {number_match}")
 
        # ── CHECK 4: Verhoeff on card number ──────────
        verhoeff_passed = None
        if number_on_card:
            verhoeff_passed = verhoeff_validate(number_on_card)
            if not verhoeff_passed:
                fraud_score -= 60
                flags.append(f"❌ [VERHOEFF] Number on card ({number_on_card}) FAILS checksum — TAMPERED")
            details['verhoeff'] = {
                'number_checked': number_on_card,
                'passed':         verhoeff_passed,
                'meaning': '✓ Valid' if verhoeff_passed else '❌ TAMPERED'
            }
        else:
            details['verhoeff'] = {'note': 'Skipped — number not detected on card'}
 
        # ── CHECK 5: ELA (images only, not PDF) ───────
        if not is_pdf:
            ela = check_ela(img)
            details['ela'] = ela
            if ela.get('suspicious'):
                fraud_score -= 15
                flags.append(f"⚠️ [ELA] High compression variance (std={ela.get('std')}) — possible editing")
 
        # ── CHECK 6: Noise Consistency ─────────────────
        noise = check_noise_consistency(img)
        details['noise'] = noise
        if noise.get('suspicious'):
            fraud_score -= 15
            flags.append(f"⚠️ [NOISE] Inconsistent noise pattern (std={noise.get('noise_std')}) — possible tampering")
 
        # ── CHECK 7: Copy-Move Detection ──────────────
        copy_move = check_copy_move(img)
        details['copyMove'] = copy_move
        if copy_move.get('suspicious'):
            fraud_score -= 20
            flags.append(f"⚠️ [COPY-MOVE] Duplicate blocks detected ({copy_move.get('duplicate_blocks')}) — possible copy-paste")
 
        # ── FINAL VERDICT ─────────────────────────────
        fraud_score = max(0, min(100, fraud_score))
 
        verhoeff_failed = verhoeff_passed is False
        
        if verhoeff_failed:
            forged        = True
            trust_level   = 'Low'
            final_verdict = f"❌ TAMPERED — Number on card ({number_on_card}) fails mathematical checksum"
        elif fraud_score >= 75:
            forged        = False
            trust_level   = 'High'
            final_verdict = '✅ GENUINE — No tampering detected'
        elif fraud_score >= 50:
            forged        = False
            trust_level   = 'Medium'
            final_verdict = '⚠️ SUSPICIOUS — Manual review recommended'
        else:
            forged        = True
            trust_level   = 'Low'
            final_verdict = '❌ FORGED — Multiple fraud signals detected'
 
        print(f"[FORENSIC] Score: {fraud_score}, Forged: {forged}")
 
        return jsonify({
            "success": True,
            "numberCheck": {
                "enteredNumber": entered_number,
                "numberOnCard":  number_on_card or 'Not detected',
                "match":         number_match,
                "verdict": (
                    '✅ Aadhaar number matches the card'                          if number_match
                    else '⚠️ Could not detect number from document'               if not number_on_card
                    else '⚠️ Aadhaar number mismatch — entered number does not match card'
                )
            },
            "forensicAnalysis": {
                "forensicScore": fraud_score,
                "trustLevel":    trust_level,
                "forged":        forged,
                "verdict":       final_verdict,
                "totalFlags":    len(flags),
                "flags":         flags,
                "details":       details
            },
            "message": final_verdict
        })
 
    except Exception as e:
        print(f"[FORENSIC ERROR] {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500
 
 
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
