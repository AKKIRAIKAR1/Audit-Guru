"""Conservative display hints from explicitly labeled OCR lines, not verified accounting data."""
import re
from datetime import datetime


LABELS = {
    'invoice_number': r'(?:invoice\s*(?:no\.?|number|#)|inv\s*(?:no\.?|#))',
    'invoice_date': r'(?:(?:invoice\s*)?date(?:\s*(?:&|and)\s*time)?)',
    'vendor': r'(?:vendor(?:\s*name)?|supplier(?:\s*name)?|seller(?:\s*name)?|from)',
    'customer': r'(?:customer(?:\s*name)?|bill(?:ed)?\s*to|buyer(?:\s*name)?|recipient(?:\s*name)?)',
    'phone': r'(?:mobile(?:\s*no\.?)?|phone(?:\s*no\.?)?|tel(?:ephone)?|contact(?:\s*no\.?)?)',
    'amount': r'(?:grand\s*total|invoice\s*total|final\s*amount|net\s*(?:amount|payable)|total\s*amount|total)',
    'tax': r'(?:total\s*tax|gst|tax)',
    'balance': r'(?:balance(?:\s*due)?|amount\s*due)',
    'subtotal': r'(?:sub\s*total|taxable\s*(?:amount|value))',
    'due_date': r'(?:due\s*date|payment\s*due\s*date)',
    'email': r'(?:e-?mail(?:\s*address)?)',
    'tax_id': r'(?:gstin|gst\s*(?:no\.?|number)|tax\s*id|vat\s*(?:no\.?|number))',
    'purchase_order': r'(?:purchase\s*order(?:\s*(?:no\.?|number))?|p\.?o\.?\s*(?:no\.?|number|#))',
    'currency': r'(?:currency)',
}

MONEY = re.compile(r'(?:INR|USD|EUR|GBP|Rs\.?)?\s*[₹$€£]?\s*-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?(?:\s*(?:INR|USD|EUR|GBP))?', re.I)
LABEL = re.compile(r'(?<!\w)(' + '|'.join(LABELS.values()) + r')(?=\s|[:#=]|$)', re.I)
STOP = re.compile(r'^(?:address|state|bank|description|material|terms|received|discount|shipping|ship\s*to)\b', re.I)


def clean_value(field, value):
    value = ' '.join(value.strip(' :#=\t').split())
    boundary = LABEL.search(value)
    if boundary:
        value = value[:boundary.start()].strip()
    if not value or STOP.match(value):
        return None
    if field in {'amount', 'subtotal', 'tax', 'balance'}:
        match = MONEY.fullmatch(value)
        return match.group().strip() if match else None
    if field in {'invoice_date', 'due_date'}:
        match = re.match(r'(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/ ](?:[A-Za-z]{3,9}|\d{1,2})[-/ ]\d{2,4})(?!\d)', value)
        if not match:
            return None
        date = match.group(1)
        for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y', '%d/%m/%y', '%d-%m-%y'):
            try:
                datetime.strptime(date, fmt)
                return date
            except ValueError:
                pass
        return None
    if field in {'invoice_number', 'purchase_order'}:
        # One identifier, never a whole customer/address sentence. Mixed-case suffixes
        # glued to numeric invoice IDs are ambiguous without geometry.
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/._-]*', value):
            return None
        if re.search(r'\d[a-z]{2,}', value):
            return None
    if field == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        return None
    if field == 'phone' and (not re.fullmatch(r'\+?[\d ()-]+', value) or not 7 <= len(re.sub(r'\D', '', value)) <= 15):
        return None
    if field in {'customer', 'vendor'} and (not re.search(r'[A-Za-z\u0080-\uffff]', value) or ':' in value):
        return None
    return value


def layout_lines(page):
    """Preserve PDF/OCR line identity instead of flattening independent columns."""
    groups = {}
    for index, word in enumerate(page.get('words', [])):
        if len(word.get('bounding_box', [])) == 4:
            groups.setdefault(word.get('line_id', str(index)), []).append(word)
    lines = []
    for group in groups.values():
        group.sort(key=lambda w: w['bounding_box'][0])
        chunks = [[]]
        for word in group:
            box = word['bounding_box']
            if chunks[-1]:
                previous = chunks[-1][-1]['bounding_box']
                if box[0] - previous[2] > 3 * max(box[3]-box[1], previous[3]-previous[1]):
                    chunks.append([])
            chunks[-1].append(word)
        for chunk in chunks:
            boxes = [word['bounding_box'] for word in chunk]
            lines.append({'text': ' '.join(word['text'] for word in chunk),
                          'box': [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                  max(b[2] for b in boxes), max(b[3] for b in boxes)]})
    return sorted(lines, key=lambda line: (line['box'][1], line['box'][0]))


def label_match(line):
    for field, pattern in LABELS.items():
        match = re.match(r'^' + pattern + r'(?=\s|[:#=]|$)\s*[:#=]?\s*', line, re.I)
        if match:
            return field, match
    return None, None


def collect(lines, positioned=False):
    candidates = {field: [] for field in LABELS}
    for index, line in enumerate(lines):
        text = line['text']
        field, match = label_match(text)
        if not field:
            continue
        raw = text[match.end():].strip()
        value = clean_value(field, raw)
        if not raw:
            if positioned:
                x1,y1,x2,y2 = line['box']
                height = max(1, y2-y1)
                right = [other for other in lines if other is not line and other['box'][0] >= x2
                         and abs((other['box'][1]+other['box'][3]-y1-y2)/2) < height * .35]
                below = [other for other in lines if other is not line and 0 < other['box'][1]-y1 <= height*2.2
                         and abs(other['box'][0]-x1) <= height*2]
                # Monetary values often live in a separately aligned right-hand cell.
                nearby = sorted(right, key=lambda l:l['box'][0])
                if not nearby or label_match(nearby[0]['text'])[0]:
                    nearby = sorted(below, key=lambda l:l['box'][1])
            else:
                nearby = lines[index+1:index+2]
            if nearby and not label_match(nearby[0]['text'])[0]:
                value = clean_value(field, nearby[0]['text'])
        if value:
            priority = 2 if field == 'amount' and re.match(r'(grand|final|net|invoice|total\s*amount)', text, re.I) else 1
            if field == 'phone' and re.match(r'contact', text, re.I):
                priority = 2
            candidates[field].append((priority, value))
    return candidates


def invoice_summary(text, pages=None):
    candidates = {field: [] for field in LABELS}
    if pages:
        for page in pages:
            positioned = layout_lines(page)
            extracted = collect(positioned, True) if positioned else collect(
                [{'text': line.strip()} for line in page.get('text', '').splitlines() if line.strip()])
            for field in candidates:
                candidates[field].extend(extracted[field])
    else:
        # Split widely spaced text columns, but retain ordinary spaces in names.
        lines = [{'text': part.strip()} for line in text.splitlines()
                 for part in re.split(r'\s{3,}', line.strip()) if part.strip()]
        candidates = collect(lines)
    values = {}
    for field, matches in candidates.items():
        priority = max((rank for rank, _ in matches), default=0)
        unique = list(dict.fromkeys(value for rank, value in matches if rank == priority))
        values[field] = unique[0] if len(unique) == 1 else None
    return values


