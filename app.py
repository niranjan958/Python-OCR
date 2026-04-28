from flask import Flask, request, jsonify, Response
import pytesseract
from PIL import Image
import fitz
import io
import re
import os
from datetime import datetime

# PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import KeepTogether

app = Flask(__name__)

# ────────────────────────────────────────
# COLORS
# ────────────────────────────────────────
DARK_BG    = colors.HexColor('#0a0d12')
SURFACE    = colors.HexColor('#111520')
BORDER     = colors.HexColor('#1e2a40')
CYAN       = colors.HexColor('#06b6d4')
GREEN      = colors.HexColor('#10b981')
RED        = colors.HexColor('#ef4444')
AMBER      = colors.HexColor('#f59e0b')
BLUE       = colors.HexColor('#2563eb')
TEXT_PRI   = colors.HexColor('#e2e8f0')
TEXT_SEC   = colors.HexColor('#7a8ba8')
TEXT_MUTED = colors.HexColor('#3d4f6b')
WHITE      = colors.white

# ────────────────────────────────────────
# OCR HELPERS (existing)
# ────────────────────────────────────────
def ocr_image_bytes(image_bytes):
    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image, lang='eng', config='--psm 6')

def ocr_pdf_bytes(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    all_text = ''
    for page in doc:
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
        text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
        all_text += text + '\n'
    doc.close()
    return all_text

def find_aadhaar_numbers(text):
    spaced   = re.findall(r'\b\d{4}\s\d{4}\s\d{4}\b', text)
    spaced   = [n.replace(' ', '') for n in spaced]
    unspaced = re.findall(r'\b\d{12}\b', text)
    all_nums = list(dict.fromkeys(spaced + unspaced))
    return [n for n in all_nums if not (n.startswith('91') and n[2] in '6789') and not n.startswith('0')]

def search_in_text(text, entered):
    if not text or not entered or len(entered) != 12:
        return False
    if entered in re.sub(r'\s+', '', text):
        return True
    spaced = f"{entered[:4]} {entered[4:8]} {entered[8:12]}"
    if spaced in text:
        return True
    dashed = f"{entered[:4]}-{entered[4:8]}-{entered[8:12]}"
    if dashed in text:
        return True
    return False

# ────────────────────────────────────────
# PDF REPORT GENERATOR
# ────────────────────────────────────────
def mask_aadhaar(num):
    if not num or len(num) < 4:
        return num or '—'
    return 'XXXX XXXX ' + num[-4:]

def status_color(status):
    if status in ['Verified', 'No', 'GENUINE', 'CLEAR', 'UNIQUE']:
        return GREEN
    if status in ['Failed', 'Yes', 'FORGED', 'DUPLICATE']:
        return RED
    return AMBER

def generate_pdf_report(data):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm
    )

    # ── Styles ──
    styles = getSampleStyleSheet()
    def sty(name, **kw):
        return ParagraphStyle(name, **kw)

    s_org    = sty('org',    fontName='Helvetica',      fontSize=7,  textColor=CYAN,     spaceAfter=2)
    s_title  = sty('title',  fontName='Helvetica-Bold', fontSize=16, textColor=TEXT_PRI, spaceAfter=4)
    s_sub    = sty('sub',    fontName='Helvetica',      fontSize=8,  textColor=TEXT_SEC, spaceAfter=0)
    s_label  = sty('label',  fontName='Helvetica',      fontSize=7,  textColor=TEXT_MUTED, spaceAfter=2)
    s_val    = sty('val',    fontName='Helvetica-Bold', fontSize=11, textColor=TEXT_PRI, spaceAfter=0)
    s_sec    = sty('sec',    fontName='Helvetica-Bold', fontSize=8,  textColor=TEXT_MUTED, spaceAfter=6)
    s_body   = sty('body',   fontName='Helvetica',      fontSize=8,  textColor=TEXT_SEC, leading=13)
    s_bold   = sty('bold',   fontName='Helvetica-Bold', fontSize=9,  textColor=TEXT_PRI)
    s_flag   = sty('flag',   fontName='Helvetica',      fontSize=8,  textColor=TEXT_SEC, leading=12)
    s_rec    = sty('rec',    fontName='Helvetica',      fontSize=8,  textColor=TEXT_SEC, leading=13, spaceAfter=4)
    s_right  = sty('right',  fontName='Helvetica',      fontSize=7,  textColor=TEXT_MUTED, alignment=TA_RIGHT)
    s_center = sty('center', fontName='Helvetica-Bold', fontSize=9,  textColor=TEXT_PRI, alignment=TA_CENTER)

    # ── Pull data ──
    name          = data.get('name', '—')
    aadhaar       = data.get('aadhaar_number', '')
    phone         = data.get('phone', '—')
    detected      = data.get('detected_number', '')
    confidence    = data.get('confidence', '0')
    mismatch      = data.get('number_mismatch', 'Unknown')   # Yes/No/Unknown
    is_duplicate  = data.get('is_duplicate', '0')
    v_status      = data.get('verification_status', 'Pending')
    forensic_score= data.get('forensic_score', '0')
    trust_level   = data.get('trust_level', 'Unknown')
    forged        = data.get('document_forged', 'No')
    match_status  = data.get('number_match_status', '')
    flags_raw     = data.get('forensic_flags', '')
    report_id     = data.get('report_id', 'ZS-' + datetime.now().strftime('%Y') + '-AA-' + datetime.now().strftime('%f')[:6])
    submitted_at  = data.get('submitted_at', datetime.now().strftime('%d %b %Y %I:%M %p'))

    dup_count  = int(is_duplicate) if str(is_duplicate).isdigit() else 0
    f_score    = int(forensic_score) if str(forensic_score).isdigit() else 0
    conf_val   = int(confidence) if str(confidence).isdigit() else 0

    # Verdict color
    v_color = GREEN if v_status == 'Verified' else RED if v_status == 'Failed' else AMBER
    v_label = '✓  VERIFIED' if v_status == 'Verified' else '✗  FAILED' if v_status == 'Failed' else '⚠  PENDING REVIEW'

    elements = []

    # ═══════════════════════════════════════
    # HEADER
    # ═══════════════════════════════════════
    header_data = [[
        [
            Paragraph('TRRAIN  ·  ZOLASHIELD  ·  EDZOLA', s_org),
            Paragraph('Aadhaar Forensic Verification Report', s_title),
            Paragraph('DOCUMENT INTEGRITY &amp; IDENTITY VERIFICATION ANALYSIS', s_sub),
        ],
        [
            Paragraph(v_label, ParagraphStyle('vl', fontName='Helvetica-Bold', fontSize=11,
                textColor=v_color, alignment=TA_RIGHT, spaceAfter=4)),
            Paragraph(f'REPORT ID: {report_id}', s_right),
            Paragraph(f'GENERATED: {datetime.now().strftime("%d %b %Y %I:%M %p")} IST', s_right),
        ]
    ]]
    header_table = Table(header_data, colWidths=[110*mm, 70*mm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND',  (0,0), (-1,-1), SURFACE),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [SURFACE]),
        ('VALIGN',      (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING',(0,0), (-1,-1), 8),
        ('TOPPADDING',  (0,0), (-1,-1), 10),
        ('BOTTOMPADDING',(0,0),(-1,-1), 10),
        ('LINEBELOW',   (0,0), (-1,0), 0.5, BORDER),
        ('LINEABOVE',   (0,0), (-1,0), 2,   BLUE),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6*mm))

    # ═══════════════════════════════════════
    # IDENTITY STRIP
    # ═══════════════════════════════════════
    id_data = [[
        [Paragraph('FULL NAME', s_label),     Paragraph(name,                  s_val)],
        [Paragraph('AADHAAR NUMBER', s_label),Paragraph(mask_aadhaar(aadhaar), ParagraphStyle('av', fontName='Helvetica-Bold', fontSize=11, textColor=CYAN))],
        [Paragraph('PHONE', s_label),         Paragraph(phone,                 s_val)],
        [Paragraph('SUBMITTED', s_label),     Paragraph(submitted_at,          s_val)],
    ]]
    id_table = Table(id_data, colWidths=[45*mm, 45*mm, 35*mm, 55*mm])
    id_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), SURFACE),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING',   (0,0), (-1,-1), 8),
        ('BOTTOMPADDING',(0,0), (-1,-1), 8),
        ('LINEAFTER',    (0,0), (2,0), 0.5, BORDER),
        ('BOX',          (0,0), (-1,-1), 0.5, BORDER),
    ]))
    elements.append(id_table)
    elements.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════
    # SECTION 1 — VERIFICATION CHECKS
    # ═══════════════════════════════════════
    elements.append(Paragraph('01  ·  VERIFICATION CHECKS', s_sec))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=4))

    # Check 1 — Number Match
    num_color = GREEN if mismatch == 'No' else RED if mismatch == 'Yes' else AMBER
    num_label = 'MATCHED' if mismatch == 'No' else 'MISMATCH' if mismatch == 'Yes' else 'UNKNOWN'
    num_icon  = '✓' if mismatch == 'No' else '✗' if mismatch == 'Yes' else '△'

    c1_findings = f"""
• Entered number: {aadhaar or '—'}<br/>
• Detected on card: {detected or 'Could not detect'}<br/>
• OCR Confidence: {conf_val}%<br/>
• Result: {match_status or ('Number matches' if mismatch == 'No' else 'Number does not match' if mismatch == 'Yes' else 'Could not verify')}
"""
    # Check 2 — Duplicate
    dup_color = GREEN if dup_count == 0 else RED
    dup_label = 'UNIQUE' if dup_count == 0 else 'DUPLICATE'
    c2_findings = f"""
• Duplicate records found: {dup_count}<br/>
• {'No duplicate Aadhaar found in system' if dup_count == 0 else f'{dup_count} existing record(s) with same Aadhaar'}<br/>
• {'Fresh unique submission' if dup_count == 0 else 'Duplicate submission — review existing records'}
"""
    # Check 3 — Forgery
    forg_color = RED if forged == 'Yes' else GREEN if f_score >= 75 else AMBER
    forg_label = 'FORGED' if forged == 'Yes' else 'GENUINE' if f_score >= 75 else 'SUSPICIOUS'
    flags_list = [f.strip() for f in flags_raw.replace('[','').replace(']','').replace('"','').split(',') if f.strip()] if flags_raw else []
    c3_findings = '<br/>'.join([f'• {f}' for f in flags_list]) if flags_list else '• No editing software metadata detected<br/>• File signature matches claimed format<br/>• No tampering signals found'

    checks_data = [
        # Row headers
        [
            Paragraph(f'<font color="#3d4f6b">Check 1 · OCR Number Match</font>', s_label),
            Paragraph(f'<font color="#3d4f6b">Check 2 · Deduplication</font>', s_label),
            Paragraph(f'<font color="#3d4f6b">Check 3 · Metadata Analysis</font>', s_label),
        ],
        [
            Paragraph(f'Aadhaar Number Verification', s_bold),
            Paragraph(f'Duplicate Submission Detection', s_bold),
            Paragraph(f'Forgery &amp; Tampering Detection', s_bold),
        ],
        [
            Paragraph(f'<font color="#{num_color.hexval()[2:]}">{num_icon} {num_label}</font>',
                ParagraphStyle('st', fontName='Helvetica-Bold', fontSize=9, textColor=num_color)),
            Paragraph(f'<font>{"✓" if dup_count==0 else "✗"} {dup_label}</font>',
                ParagraphStyle('st2', fontName='Helvetica-Bold', fontSize=9, textColor=dup_color)),
            Paragraph(f'{"✓" if forged!="Yes" and f_score>=75 else "✗" if forged=="Yes" else "△"} {forg_label}',
                ParagraphStyle('st3', fontName='Helvetica-Bold', fontSize=9, textColor=forg_color)),
        ],
        [
            Paragraph(c1_findings, s_flag),
            Paragraph(c2_findings, s_flag),
            Paragraph(c3_findings, s_flag),
        ],
    ]
    checks_table = Table(checks_data, colWidths=[60*mm, 60*mm, 60*mm])
    checks_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), SURFACE),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING',   (0,0), (2,0),   8),
        ('TOPPADDING',   (0,3), (2,3),   6),
        ('BOTTOMPADDING',(0,3), (2,3),   10),
        ('LINEAFTER',    (0,0), (1,-1),  0.5, BORDER),
        ('BOX',          (0,0), (-1,-1), 0.5, BORDER),
        ('BACKGROUND',   (0,2), (0,2),   colors.HexColor('#0d1a0f') if num_color==GREEN else colors.HexColor('#1a0d0d')),
        ('BACKGROUND',   (1,2), (1,2),   colors.HexColor('#0d1a0f') if dup_color==GREEN else colors.HexColor('#1a0d0d')),
        ('BACKGROUND',   (2,2), (2,2),   colors.HexColor('#0d1a0f') if forg_color==GREEN else colors.HexColor('#1a0d1a') if forg_color==AMBER else colors.HexColor('#1a0d0d')),
    ]))
    elements.append(checks_table)
    elements.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════
    # SECTION 2 — RISK SCORE
    # ═══════════════════════════════════════
    elements.append(Paragraph('02  ·  RISK ASSESSMENT', s_sec))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=4))

    score_color = GREEN if f_score >= 75 else AMBER if f_score >= 50 else RED
    score_data = [[
        [
            Paragraph('FORENSIC TRUST SCORE', s_label),
            Paragraph(f'{f_score} / 100', ParagraphStyle('sc', fontName='Helvetica-Bold',
                fontSize=28, textColor=score_color, spaceAfter=2)),
            Paragraph(f'Trust Level: {trust_level}', ParagraphStyle('tl', fontName='Helvetica',
                fontSize=8, textColor=score_color)),
        ],
        Table([
            [
                Paragraph('Number\nMatch', ParagraphStyle('bl', fontName='Helvetica', fontSize=7, textColor=TEXT_MUTED, alignment=TA_CENTER)),
                Paragraph('Duplicate\nCheck', ParagraphStyle('bl', fontName='Helvetica', fontSize=7, textColor=TEXT_MUTED, alignment=TA_CENTER)),
                Paragraph('Forgery\nCheck', ParagraphStyle('bl', fontName='Helvetica', fontSize=7, textColor=TEXT_MUTED, alignment=TA_CENTER)),
                Paragraph('Final\nStatus', ParagraphStyle('bl', fontName='Helvetica', fontSize=7, textColor=TEXT_MUTED, alignment=TA_CENTER)),
            ],
            [
                Paragraph('✓' if mismatch=='No' else '✗', ParagraphStyle('bv', fontName='Helvetica-Bold', fontSize=18, textColor=GREEN if mismatch=='No' else RED, alignment=TA_CENTER)),
                Paragraph('✓' if dup_count==0 else '✗', ParagraphStyle('bv2', fontName='Helvetica-Bold', fontSize=18, textColor=GREEN if dup_count==0 else RED, alignment=TA_CENTER)),
                Paragraph('✓' if forged!='Yes' else '✗', ParagraphStyle('bv3', fontName='Helvetica-Bold', fontSize=18, textColor=GREEN if forged!='Yes' else RED, alignment=TA_CENTER)),
                Paragraph('✓' if v_status=='Verified' else '✗' if v_status=='Failed' else '?', ParagraphStyle('bv4', fontName='Helvetica-Bold', fontSize=18, textColor=GREEN if v_status=='Verified' else RED if v_status=='Failed' else AMBER, alignment=TA_CENTER)),
            ],
        ], colWidths=[22*mm]*4, rowHeights=[10*mm, 14*mm])
    ]]
    score_table = Table(score_data, colWidths=[55*mm, 125*mm])
    score_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), SURFACE),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',  (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING',   (0,0), (-1,-1), 10),
        ('BOTTOMPADDING',(0,0), (-1,-1), 10),
        ('LINEAFTER',    (0,0), (0,-1),  0.5, BORDER),
        ('BOX',          (0,0), (-1,-1), 0.5, BORDER),
    ]))
    elements.append(score_table)
    elements.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════
    # SECTION 3 — FLAGS
    # ═══════════════════════════════════════
    elements.append(Paragraph('03  ·  ANOMALY &amp; FLAG LOG', s_sec))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=4))

    all_flags = []
    if forged == 'Yes':
        for f in flags_list:
            all_flags.append(('HIGH', f, 'metadata-scan'))
    if dup_count > 0:
        all_flags.append(('HIGH', f'{dup_count} duplicate record(s) found with same Aadhaar number', 'dedup-engine'))
    if mismatch == 'Yes':
        all_flags.append(('HIGH', 'Aadhaar number on card does not match entered number', 'ocr-matcher'))
    if mismatch == 'Unknown':
        all_flags.append(('MEDIUM', 'Could not extract number from document for comparison', 'ocr-engine'))
    if 50 <= f_score < 75:
        all_flags.append(('MEDIUM', f'Low forensic score ({f_score}/100) — document may be suspicious', 'forensic-engine'))

    if not all_flags:
        all_flags.append(('CLEAR', 'No anomalies detected. Document passed all checks.', 'system'))

    flags_header = [
        Paragraph('SEVERITY', s_label),
        Paragraph('DETECTION EVENT', s_label),
        Paragraph('MODULE', s_label),
    ]
    flags_rows = [flags_header]
    for sev, msg, src in all_flags:
        sev_color = RED if sev == 'HIGH' else AMBER if sev == 'MEDIUM' else GREEN if sev == 'CLEAR' else BLUE
        flags_rows.append([
            Paragraph(sev, ParagraphStyle('fs', fontName='Helvetica-Bold', fontSize=7, textColor=sev_color)),
            Paragraph(msg, s_flag),
            Paragraph(src, ParagraphStyle('fm', fontName='Helvetica', fontSize=7, textColor=TEXT_MUTED)),
        ])

    flags_table = Table(flags_rows, colWidths=[22*mm, 130*mm, 28*mm])
    flag_styles = [
        ('BACKGROUND',   (0,0), (-1,0),  colors.HexColor('#161c2d')),
        ('BACKGROUND',   (0,1), (-1,-1), SURFACE),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING',   (0,0), (-1,-1), 6),
        ('BOTTOMPADDING',(0,0), (-1,-1), 6),
        ('LINEAFTER',    (0,0), (1,-1),  0.5, BORDER),
        ('BOX',          (0,0), (-1,-1), 0.5, BORDER),
        ('ROWBACKGROUNDS',(0,1),(-1,-1), [SURFACE, colors.HexColor('#0e1420')]),
    ]
    flags_table.setStyle(TableStyle(flag_styles))
    elements.append(flags_table)
    elements.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════
    # SECTION 4 — RECOMMENDATION
    # ═══════════════════════════════════════
    elements.append(Paragraph('04  ·  RECOMMENDATION', s_sec))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=4))

    if v_status == 'Verified':
        rec_title = '✓  Accept Document — Proceed with Onboarding'
        rec_color = GREEN
        rec_body  = f'This document has passed all verification checks. The Aadhaar number matches the card, no duplicate submission was detected, and the forensic trust score of {f_score}/100 is above the acceptable threshold of 75. The document can be accepted for onboarding.'
    elif v_status == 'Failed':
        rec_title = '⚑  Reject Document — Escalate for Manual Review'
        rec_color = RED
        reasons = []
        if forged == 'Yes':    reasons.append('document appears forged or tampered')
        if mismatch == 'Yes':  reasons.append('Aadhaar number does not match card')
        if dup_count > 0:      reasons.append(f'{dup_count} duplicate record(s) found')
        rec_body = f'This document has failed critical verification checks ({"; ".join(reasons)}). The overall trust score of {f_score}/100 falls below the acceptable threshold. Reject this submission and escalate for manual review. Do not return original document pending investigation.'
    else:
        rec_title = '△  Manual Review Required — Could Not Auto-Verify'
        rec_color = AMBER
        rec_body  = f'This document could not be fully verified automatically. The forensic score of {f_score}/100 requires human review. Please have an authorised officer manually inspect the submitted Aadhaar document before proceeding.'

    rec_data = [[
        Paragraph(rec_title, ParagraphStyle('rt', fontName='Helvetica-Bold', fontSize=10, textColor=rec_color, spaceAfter=6)),
        '',
    ],[
        Paragraph(rec_body, s_rec),
        '',
    ]]
    rec_table = Table(rec_data, colWidths=[165*mm, 15*mm])
    rec_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), SURFACE),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (-1,-1), 12),
        ('TOPPADDING',   (0,0), (-1,-1), 10),
        ('BOTTOMPADDING',(0,-1),(-1,-1), 12),
        ('BOX',          (0,0), (-1,-1), 0.5, BORDER),
        ('LINEABOVE',    (0,0), (-1,0),  2, rec_color),
        ('SPAN',         (0,0), (-1,0)),
        ('SPAN',         (0,1), (-1,1)),
    ]))
    elements.append(rec_table)
    elements.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════
    footer_data = [[
        Paragraph(
            f'REPORT ID: {report_id}  ·  GENERATED: {datetime.now().strftime("%d %b %Y %I:%M %p")} IST<br/>'
            'THIS REPORT IS GENERATED BY AN AUTOMATED FORENSIC ENGINE. REVIEW BY AN AUTHORISED OFFICER BEFORE FINAL ACTION.<br/>'
            'CONFIDENTIAL  ·  FOR INTERNAL USE ONLY  ·  NOT FOR DISTRIBUTION',
            ParagraphStyle('fl', fontName='Helvetica', fontSize=6.5, textColor=TEXT_MUTED, leading=10)
        ),
        Paragraph('Powered by<br/><b>ZOLASHIELD · EDZOLA</b>',
            ParagraphStyle('fr', fontName='Helvetica', fontSize=7, textColor=CYAN, alignment=TA_RIGHT, leading=11)),
    ]]
    footer_table = Table(footer_data, colWidths=[130*mm, 50*mm])
    footer_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), SURFACE),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',  (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING',   (0,0), (-1,-1), 8),
        ('BOTTOMPADDING',(0,0), (-1,-1), 8),
        ('LINEABOVE',    (0,0), (-1,0),  0.5, BORDER),
    ]))
    elements.append(footer_table)

    doc.build(elements)
    buf.seek(0)
    return buf

# ────────────────────────────────────────
# REPORT ROUTE
# ────────────────────────────────────────
@app.route('/report', methods=['GET'])
def report():
    """
    Generates PDF report from query params.
    Deluge opens this URL with record field values.
    Example:
    https://python-ocr-1.onrender.com/report?name=John&aadhaar_number=XXXX&...
    """
    data = {
        'name':                request.args.get('name', '—'),
        'aadhaar_number':      request.args.get('aadhaar_number', ''),
        'phone':               request.args.get('phone', '—'),
        'detected_number':     request.args.get('detected_number', ''),
        'confidence':          request.args.get('confidence', '0'),
        'number_mismatch':     request.args.get('number_mismatch', 'Unknown'),
        'is_duplicate':        request.args.get('is_duplicate', '0'),
        'verification_status': request.args.get('verification_status', 'Pending'),
        'forensic_score':      request.args.get('forensic_score', '0'),
        'trust_level':         request.args.get('trust_level', 'Unknown'),
        'document_forged':     request.args.get('document_forged', 'No'),
        'number_match_status': request.args.get('number_match_status', ''),
        'forensic_flags':      request.args.get('forensic_flags', ''),
        'report_id':           request.args.get('report_id', ''),
        'submitted_at':        request.args.get('submitted_at', ''),
    }

    pdf_buf = generate_pdf_report(data)
    filename = f"Aadhaar_Report_{data['name'].replace(' ','_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

    return Response(
        pdf_buf.read(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

# ────────────────────────────────────────
# OCR ROUTE (existing)
# ────────────────────────────────────────
@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        if 'content' not in request.files:
            return jsonify({'error': 'No file uploaded', 'aadhaarNumbers': [], 'rawText': '', 'enteredFound': False}), 400

        file         = request.files['content']
        file_bytes   = file.read()
        filename     = (file.filename or '').lower()
        entered      = request.form.get('entered_number', '').strip()

        raw_text = ocr_pdf_bytes(file_bytes) if filename.endswith('.pdf') else ocr_image_bytes(file_bytes)
        print(f"[OCR] snippet: {raw_text[:150]!r}")

        aadhaar_numbers = find_aadhaar_numbers(raw_text)
        print(f"[OCR] extracted: {aadhaar_numbers}")

        entered_found = False
        if entered and len(entered) == 12:
            entered_found = search_in_text(raw_text, entered)
            print(f"[OCR] entered={entered} found={entered_found}")

        return jsonify({
            'aadhaarNumbers': aadhaar_numbers,
            'rawText':        raw_text,
            'enteredFound':   entered_found,
            'success':        True
        })

    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({'error': str(e), 'aadhaarNumbers': [], 'rawText': '', 'enteredFound': False}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
