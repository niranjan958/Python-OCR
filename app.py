from flask import Flask, request, jsonify, Response
import pytesseract
from PIL import Image
import fitz
import io
import re
import os
import json
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable, KeepTogether
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

app = Flask(__name__)

# ─────────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────────
BG      = colors.HexColor('#0a0d12')
SURFACE = colors.HexColor('#111520')
SURF2   = colors.HexColor('#161c2d')
BORDER  = colors.HexColor('#1e2a40')
CYAN    = colors.HexColor('#06b6d4')
GREEN   = colors.HexColor('#10b981')
RED     = colors.HexColor('#ef4444')
AMBER   = colors.HexColor('#f59e0b')
BLUE    = colors.HexColor('#2563eb')
TEXT    = colors.HexColor('#e2e8f0')
MUTED   = colors.HexColor('#7a8ba8')
DIMMED  = colors.HexColor('#3d4f6b')
WHITE   = colors.white

# ─────────────────────────────────────────────
# STYLE HELPERS
# ─────────────────────────────────────────────
def ps(name, font='Helvetica', size=8, color=MUTED, bold=False,
       align=TA_LEFT, leading=12, space_after=0):
    return ParagraphStyle(
        name,
        fontName='Helvetica-Bold' if bold else font,
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading,
        spaceAfter=space_after
    )

S_ORG     = ps('org',   size=7,  color=CYAN)
S_TITLE   = ps('title', size=16, color=TEXT,  bold=True,  leading=20)
S_SUB     = ps('sub',   size=8,  color=MUTED, leading=10)
S_LABEL   = ps('lbl',   size=7,  color=DIMMED)
S_VAL     = ps('val',   size=10, color=TEXT,  bold=True,  leading=14)
S_CYAN    = ps('cyn',   size=10, color=CYAN,  bold=True,  leading=14)
S_SEC     = ps('sec',   size=8,  color=DIMMED, bold=True)
S_BODY    = ps('body',  size=8,  color=MUTED, leading=13)
S_BOLD    = ps('bld',   size=9,  color=TEXT,  bold=True)
S_SMALL   = ps('sml',   size=7,  color=MUTED, leading=11)
S_RIGHT   = ps('rgt',   size=7,  color=DIMMED, align=TA_RIGHT)
S_CENTER  = ps('ctr',   size=8,  color=TEXT,  align=TA_CENTER)
S_FLAG    = ps('flg',   size=7.5, color=MUTED, leading=12)

def status_color(status):
    s = str(status).lower()
    if s in ['verified','no','genuine','clear','unique','matched']: return GREEN
    if s in ['failed','yes','forged','duplicate','mismatch']:       return RED
    return AMBER

def mask_aadhaar(num):
    n = str(num or '')
    if len(n) < 4: return n or '—'
    return 'XXXX XXXX ' + n[-4:]

def now_str():
    return datetime.now().strftime('%d %b %Y %I:%M %p')

def gen_report_id():
    return 'ZS-' + datetime.now().strftime('%Y') + '-AA-' + datetime.now().strftime('%f')[:6]

def card_table(rows, col_widths, accent_color=None):
    """Creates a styled dark card table."""
    t = Table(rows, colWidths=col_widths)
    style = [
        ('BACKGROUND',    (0,0), (-1,-1), SURFACE),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ]
    if accent_color:
        style.append(('LINEABOVE', (0,0), (-1,0), 2, accent_color))
    t.setStyle(TableStyle(style))
    return t

# ─────────────────────────────────────────────
# PDF GENERATOR
# ─────────────────────────────────────────────
def generate_pdf(data):
    buf = io.BytesIO()
    W, H = A4
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=12*mm, bottomMargin=12*mm
    )
    FULL = W - 28*mm
    elements = []

    # ── Parse data ──────────────────────────
    name          = data.get('name', '—')
    aadhaar       = str(data.get('aadhaar_number', '') or data.get('enteredNumber', ''))
    phone         = data.get('phone', '—')
    submitted_at  = data.get('submitted_at', now_str())
    report_id     = data.get('report_id', gen_report_id())

    # numberCheck
    nc            = data.get('numberCheck', {}) or {}
    detected      = str(nc.get('numberOnCard', '') or '')
    confidence    = int(nc.get('confidence', 0) or 0)
    num_match     = nc.get('match', False)
    match_verdict = nc.get('verdict', '')

    # forensicAnalysis
    fa            = data.get('forensicAnalysis', {}) or {}
    f_score       = int(fa.get('forensicScore', 0) or 0)
    trust         = str(fa.get('trustLevel', 'Unknown') or 'Unknown')
    forged        = fa.get('forged', False)
    fa_verdict    = str(fa.get('verdict', '') or '')
    flags_list    = fa.get('flags', []) or []

    # derived
    is_duplicate  = int(data.get('is_duplicate', 0) or 0)
    v_status      = data.get('verification_status', 'Pending')
    mismatch      = 'No' if num_match else ('Yes' if detected and detected != 'Not detected' else 'Unknown')

    v_color  = status_color(v_status)
    n_color  = GREEN if num_match else (RED if mismatch == 'Yes' else AMBER)
    d_color  = GREEN if is_duplicate == 0 else RED
    f_color  = RED if forged else (GREEN if f_score >= 75 else AMBER)
    s_color  = GREEN if f_score >= 75 else (AMBER if f_score >= 50 else RED)

    v_label  = ('✓  VERIFIED' if v_status == 'Verified'
                else '✗  FAILED' if v_status == 'Failed'
                else '⚠  PENDING REVIEW')

    # ══════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════
    header = Table([[
        [
            Paragraph('TRRAIN  ·  ZOLASHIELD  ·  EDZOLA', S_ORG),
            Spacer(1, 3),
            Paragraph('Aadhaar Forensic Verification Report', S_TITLE),
            Spacer(1, 3),
            Paragraph('DOCUMENT INTEGRITY &amp; IDENTITY VERIFICATION ANALYSIS', S_SUB),
        ],
        [
            Paragraph(v_label, ps('vl', size=12, color=v_color, bold=True, align=TA_RIGHT)),
            Spacer(1, 6),
            Paragraph(f'REPORT ID: {report_id}', S_RIGHT),
            Paragraph(f'GENERATED: {now_str()} IST', S_RIGHT),
        ]
    ]], colWidths=[FULL*0.62, FULL*0.38])
    header.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), SURFACE),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LINEABOVE',     (0,0), (-1,0),  2,   BLUE),
        ('LINEBELOW',     (0,0), (-1,0),  0.5, BORDER),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ]))
    elements.append(header)
    elements.append(Spacer(1, 5*mm))

    # ══════════════════════════════════════════
    # IDENTITY STRIP
    # ══════════════════════════════════════════
    cw = FULL / 4
    id_row = [[
        [Paragraph('FULL NAME', S_LABEL), Spacer(1,3), Paragraph(name, S_VAL)],
        [Paragraph('AADHAAR NUMBER', S_LABEL), Spacer(1,3), Paragraph(mask_aadhaar(aadhaar), S_CYAN)],
        [Paragraph('PHONE', S_LABEL), Spacer(1,3), Paragraph(str(phone), S_VAL)],
        [Paragraph('SUBMITTED', S_LABEL), Spacer(1,3), Paragraph(str(submitted_at), S_VAL)],
    ]]
    id_table = Table(id_row, colWidths=[cw]*4)
    id_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), SURFACE),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
        ('LINEAFTER',     (0,0), (2,0),   0.5, BORDER),
    ]))
    elements.append(id_table)
    elements.append(Spacer(1, 5*mm))

    # ══════════════════════════════════════════
    # SECTION 1 — CHECKS
    # ══════════════════════════════════════════
    elements.append(Paragraph('01  ·  VERIFICATION CHECKS', S_SEC))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=5))

    cw3 = (FULL - 8) / 3

    # Check 1 — Number
    n_tag   = '✓  MATCHED' if num_match else ('✗  MISMATCH' if mismatch == 'Yes' else '△  UNKNOWN')
    n_lines = [
        f'Entered: {aadhaar or "—"}',
        f'Detected on card: {detected or "Not detected"}',
        f'OCR Confidence: {confidence}%',
        match_verdict or ('Matches card' if num_match else 'Does not match'),
    ]

    # Check 2 — Duplicate
    d_tag   = '✓  UNIQUE' if is_duplicate == 0 else f'✗  DUPLICATE ({is_duplicate})'
    d_lines = [
        f'Duplicate records: {is_duplicate}',
        'No duplicate found' if is_duplicate == 0 else f'{is_duplicate} record(s) with same Aadhaar',
        'Fresh unique submission' if is_duplicate == 0 else 'Review existing records',
    ]

    # Check 3 — Forgery
    f_tag   = '✗  FORGED' if forged else ('✓  GENUINE' if f_score >= 75 else '△  SUSPICIOUS')
    f_lines = flags_list[:4] if flags_list else [
        'No editing software metadata detected',
        'File signature matches claimed format',
        'No tampering signals found',
    ]

    def check_card(sub, title, tag, tag_color, lines):
        findings = [Paragraph(f'• {l}', S_SMALL) for l in lines[:4]]
        rows = [
            [Paragraph(sub, S_LABEL)],
            [Paragraph(title, S_BOLD)],
            [Paragraph(tag, ps('tg', size=8.5, color=tag_color, bold=True))],
            [HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=2)],
            *[[f] for f in findings],
        ]
        t = Table(rows, colWidths=[cw3 - 4])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,-1), SURFACE),
            ('VALIGN',        (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING',   (0,0), (-1,-1), 10),
            ('RIGHTPADDING',  (0,0), (-1,-1), 8),
            ('TOPPADDING',    (0,0), (0,0),   8),
            ('BOTTOMPADDING', (0,-1),(-1,-1), 10),
            ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
            ('LINEABOVE',     (0,0), (-1,0),  2,   tag_color),
        ]))
        return t

    checks_row = [[
        check_card('Check 1 · OCR Number Match',  'Aadhaar Number Verification',    n_tag, n_color, n_lines),
        check_card('Check 2 · Deduplication',      'Duplicate Submission Detection', d_tag, d_color, d_lines),
        check_card('Check 3 · Metadata Analysis',  'Forgery &amp; Tampering Detection', f_tag, f_color, f_lines),
    ]]
    checks_table = Table(checks_row, colWidths=[cw3]*3, hAlign='LEFT')
    checks_table.setStyle(TableStyle([
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',   (0,0), (-1,-1), 0),
        ('RIGHTPADDING',  (0,0), (-1,-1), 0),
        ('TOPPADDING',    (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('INNERGRID',     (0,0), (-1,-1), 0, colors.transparent),
        ('BOX',           (0,0), (-1,-1), 0, colors.transparent),
    ]))
    # Add spacing between cards manually
    spacer_row = [[Spacer(1,1), Spacer(1,1), Spacer(1,1)]]
    outer = Table(
        [[check_card('Check 1 · OCR Number Match', 'Aadhaar Number Verification', n_tag, n_color, n_lines),
          check_card('Check 2 · Deduplication', 'Duplicate Submission Detection', d_tag, d_color, d_lines),
          check_card('Check 3 · Metadata Analysis', 'Forgery &amp; Tampering Detection', f_tag, f_color, f_lines)]],
        colWidths=[cw3 - 2, cw3 - 2, cw3 - 2]
    )
    outer.setStyle(TableStyle([
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',   (0,0), (-1,-1), 2),
        ('RIGHTPADDING',  (0,0), (-1,-1), 2),
        ('TOPPADDING',    (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    elements.append(outer)
    elements.append(Spacer(1, 5*mm))

    # ══════════════════════════════════════════
    # SECTION 2 — RISK SCORE
    # ══════════════════════════════════════════
    elements.append(Paragraph('02  ·  RISK ASSESSMENT', S_SEC))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=5))

    b_items = [
        ('Number\nMatch',   '✓' if num_match else '✗',          GREEN if num_match else RED),
        ('Duplicate\nCheck','✓' if is_duplicate == 0 else '✗',  GREEN if is_duplicate == 0 else RED),
        ('Forgery\nCheck',  '✓' if not forged else '✗',         GREEN if not forged else RED),
        ('Final\nStatus',   '✓' if v_status == 'Verified' else '✗' if v_status == 'Failed' else '?', v_color),
    ]

    score_table = Table([[
        # Score block
        [
            Paragraph('FORENSIC TRUST SCORE', S_LABEL),
            Spacer(1, 4),
            Paragraph(str(f_score), ps('sc', size=36, color=s_color, bold=True, leading=38)),
            Paragraph(f'out of 100  ·  {trust}', ps('tl', size=7.5, color=s_color)),
        ],
        # Breakdown
        Table(
            [[Paragraph(b[0], ps(f'bl{i}', size=7, color=DIMMED, align=TA_CENTER, leading=10)) for i, b in enumerate(b_items)],
             [Paragraph(b[1], ps(f'bv{i}', size=22, color=b[2], bold=True, align=TA_CENTER)) for i, b in enumerate(b_items)]],
            colWidths=[(FULL * 0.55) / 4] * 4
        )
    ]], colWidths=[FULL * 0.4, FULL * 0.6])

    score_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), SURFACE),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',   (0,0), (0,0),   12),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
        ('LINEAFTER',     (0,0), (0,0),   0.5, BORDER),
        ('LINEABOVE',     (0,0), (-1,0),  2,   s_color),
    ]))
    elements.append(score_table)
    elements.append(Spacer(1, 5*mm))

    # ══════════════════════════════════════════
    # SECTION 3 — FLAGS
    # ══════════════════════════════════════════
    elements.append(Paragraph('03  ·  ANOMALY &amp; FLAG LOG', S_SEC))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=5))

    all_flags = []
    if forged:
        for f in flags_list:
            all_flags.append(('HIGH', str(f), 'metadata-scan'))
    if is_duplicate > 0:
        all_flags.append(('HIGH', f'{is_duplicate} duplicate record(s) found with same Aadhaar', 'dedup-engine'))
    if mismatch == 'Yes':
        all_flags.append(('HIGH', 'Aadhaar number on card does not match entered number', 'ocr-matcher'))
    if mismatch == 'Unknown':
        all_flags.append(('MEDIUM', 'Could not extract number from document for comparison', 'ocr-engine'))
    if 50 <= f_score < 75:
        all_flags.append(('MEDIUM', f'Low forensic score ({f_score}/100) — review recommended', 'forensic-engine'))
    if not all_flags:
        all_flags.append(('CLEAR', 'No anomalies detected. All checks passed successfully.', 'system'))

    sev_color = {'HIGH': RED, 'MEDIUM': AMBER, 'LOW': BLUE, 'CLEAR': GREEN}

    flag_rows = [[
        Paragraph('SEVERITY', ps('fh', size=7, color=DIMMED, bold=True)),
        Paragraph('DETECTION EVENT', ps('fh2', size=7, color=DIMMED, bold=True)),
        Paragraph('MODULE', ps('fh3', size=7, color=DIMMED, bold=True)),
    ]]
    for sev, msg, src in all_flags:
        sc = sev_color.get(sev, MUTED)
        flag_rows.append([
            Paragraph(sev, ps(f's{sev}', size=7.5, color=sc, bold=True)),
            Paragraph(msg, S_FLAG),
            Paragraph(src, ps(f'm{sev}', size=7, color=DIMMED)),
        ])

    flags_table = Table(flag_rows, colWidths=[22*mm, FULL - 22*mm - 28*mm, 28*mm])
    flag_styles = [
        ('BACKGROUND',    (0,0), (-1,0),  SURF2),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
        ('LINEAFTER',     (0,0), (1,-1),  0.5, BORDER),
        ('LINEBELOW',     (0,0), (-1,-2), 0.5, BORDER),
    ]
    for i in range(1, len(flag_rows)):
        bg = SURFACE if i % 2 == 1 else colors.HexColor('#0e1420')
        flag_styles.append(('BACKGROUND', (0,i), (-1,i), bg))
    flags_table.setStyle(TableStyle(flag_styles))
    elements.append(flags_table)
    elements.append(Spacer(1, 5*mm))

    # ══════════════════════════════════════════
    # SECTION 4 — RECOMMENDATION
    # ══════════════════════════════════════════
    elements.append(Paragraph('04  ·  RECOMMENDATION', S_SEC))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=5))

    if v_status == 'Verified':
        rec_title = '✓  Accept Document — Proceed with Onboarding'
        rec_body  = (f'This document has passed all verification checks. '
                     f'The Aadhaar number matches the card, no duplicate was detected, '
                     f'and the forensic trust score of {f_score}/100 is above the acceptable '
                     f'threshold. The document can be accepted for onboarding.')
    elif v_status == 'Failed':
        reasons = []
        if forged:           reasons.append('document appears forged or tampered')
        if mismatch == 'Yes': reasons.append('Aadhaar number does not match card')
        if is_duplicate > 0:  reasons.append(f'{is_duplicate} duplicate record(s) found')
        rec_title = '⚑  Reject Document — Escalate for Manual Review'
        rec_body  = (f'This document has failed critical verification checks '
                     f'({"; ".join(reasons) if reasons else "multiple failures"}). '
                     f'Overall trust score: {f_score}/100. '
                     f'Reject this submission and escalate for manual review.')
    else:
        rec_title = '△  Manual Review Required — Could Not Auto-Verify'
        rec_body  = (f'This document could not be fully auto-verified. '
                     f'Forensic score: {f_score}/100 ({trust}). '
                     f'Please have an authorised officer manually inspect '
                     f'the submitted Aadhaar document before proceeding.')

    rec_table = Table([[
        Paragraph(rec_title, ps('rt', size=10, color=v_color, bold=True, space_after=6)),
    ],[
        Paragraph(rec_body, S_BODY),
    ]], colWidths=[FULL])
    rec_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), SURFACE),
        ('LEFTPADDING',   (0,0), (-1,-1), 14),
        ('RIGHTPADDING',  (0,0), (-1,-1), 14),
        ('TOPPADDING',    (0,0), (-1,0),  12),
        ('BOTTOMPADDING', (0,-1),(-1,-1), 14),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
        ('LINEABOVE',     (0,0), (-1,0),  2,   v_color),
    ]))
    elements.append(rec_table)
    elements.append(Spacer(1, 5*mm))

    # ══════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════
    footer = Table([[
        Paragraph(
            f'REPORT ID: {report_id}  ·  GENERATED: {now_str()} IST<br/>'
            f'THIS REPORT IS GENERATED BY AN AUTOMATED FORENSIC ENGINE. '
            f'REVIEW BY AN AUTHORISED OFFICER BEFORE FINAL ACTION.<br/>'
            f'CONFIDENTIAL  ·  FOR INTERNAL USE ONLY  ·  NOT FOR DISTRIBUTION',
            ps('fl', size=6.5, color=DIMMED, leading=10)
        ),
        Paragraph(
            'Powered by<br/><b>ZOLASHIELD · EDZOLA</b>',
            ps('fr', size=7.5, color=CYAN, align=TA_RIGHT, leading=12)
        ),
    ]], colWidths=[FULL * 0.72, FULL * 0.28])
    footer.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), SURF2),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LINEABOVE',     (0,0), (-1,0),  0.5, BORDER),
        ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ]))
    elements.append(footer)

    doc.build(elements)
    buf.seek(0)
    return buf

# ─────────────────────────────────────────────
# REPORT ROUTE — accepts raw JSON from Creator
# ─────────────────────────────────────────────
@app.route('/report', methods=['GET'])
def report():
    """
    Accepts ?data=<url-encoded JSON string>
    JSON is the Raw_Response field from Creator
    which contains the full Catalyst API response
    plus name, phone, is_duplicate, verification_status
    added by Deluge before saving.
    """
    try:
        raw = request.args.get('data', '')
        if not raw:
            return jsonify({'error': 'No data provided'}), 400

        data = json.loads(raw)
        pdf_buf = generate_pdf(data)

        name = data.get('name', 'Report')
        filename = f"Aadhaar_Report_{name.replace(' ','_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

        return Response(
            pdf_buf.read(),
            mimetype='application/pdf',
            headers={'Content-Disposition': f'inline; filename="{filename}"'}
        )
    except Exception as e:
        print(f'[REPORT ERROR] {e}')
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# OCR ROUTE (unchanged)
# ─────────────────────────────────────────────
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
        all_text += pytesseract.image_to_string(img, lang='eng', config='--psm 6') + '\n'
    doc.close()
    return all_text

def find_aadhaar_numbers(text):
    spaced   = [n.replace(' ', '') for n in re.findall(r'\b\d{4}\s\d{4}\s\d{4}\b', text)]
    unspaced = re.findall(r'\b\d{12}\b', text)
    all_nums = list(dict.fromkeys(spaced + unspaced))
    return [n for n in all_nums if not (n.startswith('91') and n[2] in '6789') and not n.startswith('0')]

def search_in_text(text, entered):
    if not text or not entered or len(entered) != 12: return False
    if entered in re.sub(r'\s+', '', text): return True
    if f"{entered[:4]} {entered[4:8]} {entered[8:12]}" in text: return True
    if f"{entered[:4]}-{entered[4:8]}-{entered[8:12]}" in text: return True
    return False

@app.route('/ocr', methods=['POST'])
def ocr():
    try:
        if 'content' not in request.files:
            return jsonify({'error': 'No file', 'aadhaarNumbers': [], 'rawText': '', 'enteredFound': False}), 400
        file        = request.files['content']
        file_bytes  = file.read()
        filename    = (file.filename or '').lower()
        entered     = request.form.get('entered_number', '').strip()
        raw_text    = ocr_pdf_bytes(file_bytes) if filename.endswith('.pdf') else ocr_image_bytes(file_bytes)
        nums        = find_aadhaar_numbers(raw_text)
        found       = search_in_text(raw_text, entered) if entered and len(entered) == 12 else False
        return jsonify({'aadhaarNumbers': nums, 'rawText': raw_text, 'enteredFound': found, 'success': True})
    except Exception as e:
        return jsonify({'error': str(e), 'aadhaarNumbers': [], 'rawText': '', 'enteredFound': False}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
