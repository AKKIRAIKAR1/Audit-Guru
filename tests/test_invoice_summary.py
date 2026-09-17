from app.invoice_summary import invoice_summary
from app.jobs import process_job


def test_labeled_summary_preserves_amounts_and_unknowns():
    result = invoice_summary('Invoice No: INV/26/762\nInvoice Date: 12-Sep-2026\nVendor: ABC Supplies\nCustomer Name: Nice Fashion LTD.\nMobile No: 7020824191\nTotal: INR 82,918.81\nGST: 900.00\nBalance: 0.00')
    assert result['invoice_number'] == 'INV/26/762'
    assert result['invoice_date'] == '12-Sep-2026'
    assert result['amount'] == 'INR 82,918.81'
    assert result['balance'] == '0.00'
    assert result['customer'] == 'Nice Fashion LTD.'
    assert result['phone'] == '7020824191'
    assert all(value is None for value in invoice_summary('Unlabeled document text').values())


def test_conflicting_values_and_non_amounts_are_not_guessed():
    result = invoice_summary('Total: 100.00\nTotal: 200.00\nGST: 18%\nInvoice No: ABC\nInvoice No: ABC')
    assert result['amount'] is None
    assert result['tax'] is None
    assert result['invoice_number'] == 'ABC'


def test_document_grid_only_exposes_completed_owned_results(client):
    import pymupdf
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((70,70), 'Invoice No: INV-123\nVendor: ABC Supplies\nTotal: 59000')
        content = pdf.tobytes()
    auth = {'X-API-Key': 'test-a'}
    doc = client.post('/api/v1/documents', headers=auth, files={'file': ('invoice.pdf',content,'application/pdf')}).json()
    assert client.get('/api/v1/documents',headers=auth).json()['items'][0]['invoice_summary'] is None
    process_job(doc['job_id'])
    row = client.get('/api/v1/documents',headers=auth).json()['items'][0]
    assert row['invoice_summary']['invoice_number'] == 'INV-123'
    assert row['invoice_summary']['amount'] == '59000'
    details = client.get('/api/v1/ocr/result/'+doc['id'], headers=auth).json()['invoice_summary']
    assert details == row['invoice_summary']
    assert client.get('/api/v1/documents',headers={'X-API-Key':'test-b'}).json()['items'] == []


def test_basic_details_multiline_labels_and_contact():
    result = invoice_summary('Bill To:\nNice Fashion LTD.\nFrom:\nABC Supplies\nFinal Amount:\nINR 59000.00\nPhone No:\n7020824191\nEmail:\naccounts@example.com\nGSTIN: 27ABCDE1234F1Z5\nDue Date: 30-Sep-2026\nSubtotal: 50000\nPO No: PO-52')
    assert result['customer'] == 'Nice Fashion LTD.'
    assert result['vendor'] == 'ABC Supplies'
    assert result['amount'] == 'INR 59000.00'
    assert result['phone'] == '7020824191'
    assert result['email'] == 'accounts@example.com'
    assert result['tax_id'] == '27ABCDE1234F1Z5'
    assert result['purchase_order'] == 'PO-52'
    assert result['due_date'] == '30-Sep-2026'
    assert result['subtotal'] == '50000'


def test_missing_value_does_not_consume_next_field():
    result = invoice_summary('Customer:\nInvoice No: INV-5\nEmail: not-an-email\nFinal Amount:\nTax: 100')
    assert result['customer'] is None
    assert result['amount'] is None
    assert result['email'] is None
    assert result['invoice_number'] == 'INV-5'


def positioned_invoice(customer='Nice Fashion LTD.', number='INV/26-27/757', total='₹4,860.00'):
    # Source line identities and staggered columns reproduce a PDF whose flattened
    # text joins the customer to the invoice number and contact label to the date.
    entries = [
        ('Bill To :', [25,181,68,194], '3:0'),
        (customer, [25,194,175,208], '3:1'),
        ('Contact No.:', [25,211,92,225], '3:2'),
        ('Invoice No. :', [414,190,492,203], '4:0'),
        (number, [495,190,573,203], '4:0'),
        ('Date & Time :', [421,207,501,221], '5:0'),
        ('10-Sep-2026', [504,207,573,221], '5:0'),
        ('15:52:36', [526,221,573,234], '6:0'),
        ('SUB TOTAL', [356,435,422,448], '12:0'),
        ('₹4,347.46', [519,435,573,448], '12:1'),
        ('RUPEES ONLY', [32,470,117,484], '9:1'),
        ('TOTAL', [356,477,395,490], '14:0'),
        (total, [519,477,573,490], '14:1'),
        ('RECEIVED', [356,498,418,511], '15:0'),
        ('₹2,000.00', [519,498,573,511], '15:1'),
        ('BALANCE', [356,518,416,531], '16:0'),
        ('₹2,860.00', [519,518,573,531], '16:1'),
    ]
    return {'words':[{'text':text,'bounding_box':box,'line_id':line} for text,box,line in entries],
            'text':f'Bill To:\nInvoice No.: {number}{customer}\nDate & Time: 10-Sep-2026Contact No.:'}


def test_staggered_columns_and_separate_total_cells():
    page = positioned_invoice()
    result = invoice_summary(page['text'], [page])
    assert result['customer'] == 'Nice Fashion LTD.'
    assert result['invoice_number'] == 'INV/26-27/757'
    assert result['invoice_date'] == '10-Sep-2026'
    assert result['amount'] == '₹4,860.00'
    assert result['subtotal'] == '₹4,347.46'
    assert result['balance'] == '₹2,860.00'
    assert result['phone'] is None  # Empty contact label must not capture time.


def test_clean_dates_and_final_total_precedence():
    result = invoice_summary('Date & Time : 10-Sep-2026Contact No.: 1234567890\nTotal: 4000\nGrand Total: ₹4,860.00\nReceived: 2000\nBalance: 2860')
    assert result['invoice_date'] == '10-Sep-2026'
    assert result['amount'] == '₹4,860.00'
    assert invoice_summary('Invoice Date: 31-Feb-2026')['invoice_date'] is None
    assert invoice_summary('Invoice No: INV/26-27/757Nice Fashion LTD.')['invoice_number'] is None


def test_conflicts_across_pages_do_not_select_one_invoice():
    pages = [positioned_invoice(), positioned_invoice('Another Customer','INV/26-27/758','₹9,000.00')]
    result = invoice_summary('', pages)
    assert result['customer'] is None
    assert result['invoice_number'] is None
    assert result['amount'] is None


def test_same_row_labels_do_not_hide_customer_below():
    page = positioned_invoice()
    page['words'][3]['bounding_box'] = [414,181,492,194]
    page['words'][4]['bounding_box'] = [495,181,573,194]
    assert invoice_summary('', [page])['customer'] == 'Nice Fashion LTD.'
