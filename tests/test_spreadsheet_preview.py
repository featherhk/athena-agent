"""Real OOXML and HTTP previews: meaning survives without recalculating source."""
import hashlib
import io
import json
import struct
import zipfile
from xml.sax.saxutils import escape, quoteattr

import httpx
import pytest

from athena.api import create_app
from athena.spreadsheet_preview import MAX_CHARS, xlsx_preview


def workbook(rows, *, formats=(), date1904=False, calc='', sheets=1, extras=None, styles=None):
    output = io.BytesIO()
    definitions = ''.join(f'<sheet name="Quote {index}" r:id="r{index}"/>' for index in range(sheets))
    relations = ''.join(f'<Relationship Id="r{index}" Target="worksheets/sheet{index}.xml"/>' for index in range(sheets))
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr date1904="{int(date1904)}"/><sheets>{definitions}</sheets>{calc}</workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships>{relations}</Relationships>')
        for index in range(sheets):
            archive.writestr(f'xl/worksheets/sheet{index}.xml', rows if isinstance(rows, bytes) else f'<worksheet><sheetData>{rows}</sheetData></worksheet>')
        if styles is not None or formats:
            codes = ''.join(f'<numFmt numFmtId="{164 + index}" formatCode={quoteattr(code)}/>' for index, code in enumerate(formats))
            xfs = ''.join(f'<xf numFmtId="{164 + index}"/>' for index in range(len(formats)))
            archive.writestr('xl/styles.xml', styles if styles is not None else f'<styleSheet><numFmts>{codes}</numFmts><cellXfs>{xfs}</cellXfs></styleSheet>')
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def number(raw, code, **kwargs):
    result = xlsx_preview(workbook(f'<row r="1"><c r="A1"><v>{escape(raw)}</v></c></row>', formats=[code], **kwargs))
    assert result['sheets'][0]['rows'][0][0] == result['sheets'][0]['cells'][0][0]['display']
    return result['sheets'][0]['cells'][0][0]


@pytest.mark.parametrize(('raw', 'code', 'display'), [
    ('1234.5', '"¥"#,##0.00', '¥1,234.50'),
    ('1234.5', '[$USD-409] #,##0.00', 'USD 1,234.50'),
    ('1234.5', '€ #,##0.00', '€ 1,234.50'),
    ('-1234.5', '#,##0.00;[Red](#,##0.00)', '(1,234.50)'),
    ('-1234.5', '"$"#,##0.00', '-$1,234.50'),
    ('0.08', '0%', '8%'), ('-0.08', '0.00%', '-8.00%'),
    ('8', '0.0"%"', '8.0%'), ('12.5', '0.00" m²"', '12.50 m²'),
    ('12.5', '0.00" hours"', '12.50 hours'),
    ('12.5', '0.00"; quoted unit"', '12.50; quoted unit'),
    ('12', '0000', '0012'), ('12.340', '0.0##', '12.34'),
    ('2.675', '0.00', '2.68'), ('0', '0.00;[Red](0.00);"—"', '—'),
    ('0', '0;-0;', ''), ('5', ';;;', ''),
])
def test_quote_numbers_have_explicit_units_and_correct_scale(raw, code, display):
    cell = number(raw, code)
    assert cell == {'raw': raw, 'display': display, 'kind': 'number', 'format_status': 'formatted', 'number_format': code}


@pytest.mark.parametrize(('raw', 'code'), [
    ('1234', '0,'), ('0.5', '# ?/?'), ('1000', '0.00E+00'),
    ('12', '[>10]0.00;0'), ('-5', '[<0]"debit";"credit"'),
    ('-0.5', '[>100]0;0%'), ('1.5', '[h]:mm'),
    ('1', '0.00000000000'), ('1e100000', '0.00'),
    ('NaN', '0.00'), ('Infinity', '0.00'), ('1' * 101, '0.00'),
    ('1', '"unclosed'), ('1', '0\\'), ('1', '0;0;0;@;0'),
    ('46299', 'yyyy" days"'), ('46299.5', 'yyyy-mm-dd h:mm" UTC"'),
    ('46299.99999884259259', 'yyyy-mm-dd h:mm'),
    ('0.99999884259259', 'h:mm:ss'),
])
def test_unsupported_or_conditional_formats_do_not_guess(raw, code):
    cell = number(raw, code)
    assert cell['raw'] == cell['display'] == raw
    assert cell['format_status'] == 'unsupported'


@pytest.mark.parametrize(('raw', 'code', 'date1904', 'display'), [
    ('40729', 'yyyy-mm-dd', False, '2011-07-05'),
    ('39267', 'yyyy-mm-dd', True, '2011-07-05'),
    ('59', 'yyyy-mm-dd', False, '1900-02-28'),
    ('61', 'yyyy-mm-dd', False, '1900-03-01'),
    ('0', 'yyyy-mm-dd', True, '1904-01-01'),
    ('46299.5', 'yyyy-mm-dd h:mm', False, '2026-10-04 12:00'),
    ('46299.99966435185', 'yyyy-mm-dd h:mm', False, '2026-10-04 23:59'),
    ('0.5006828703703704', 'h:mm', False, '12:00'),
    ('0.5006828703703704', 'h:mm:ss', False, '12:00:59'),
    ('0.75', 'h:mm AM/PM', False, '18:00'),
    ('46299', 'yyyy"年"mm"月"dd"日"', False, '2026-10-04'),
    ('46299', 'd-mmm', False, '2026-10-04'),
])
def test_date_systems_and_time_fields_are_unambiguous(raw, code, date1904, display):
    cell = number(raw, code, date1904=date1904)
    assert cell['kind'] == 'date' and cell['format_status'] == 'formatted'
    assert cell['display'] == display


@pytest.mark.parametrize('raw', ['0', '60', '-1', '9999999999'])
def test_non_gregorian_or_invalid_dates_stay_explicitly_raw(raw):
    cell = number(raw, 'yyyy-mm-dd')
    assert cell['display'] == raw and cell['format_status'] == 'unsupported'


def test_style_defaults_inheritance_and_unsupported_styles():
    styles = '<styleSheet><cellStyleXfs><xf numFmtId="10"/></cellStyleXfs><cellXfs><xf/><xf xfId="0"/><xf numFmtId="10" applyNumberFormat="false"/><xf numFmtId="5"/><xf xfId="200"/></cellXfs></styleSheet>'
    rows = ''.join(f'<row r="{index + 1}"><c r="A{index + 1}" s="{index}"><v>0.08</v></c></row>' for index in range(6))
    result = xlsx_preview(workbook(rows, styles=styles))
    cells = [row[0] for row in result['sheets'][0]['cells']]
    assert [cell['display'] for cell in cells] == ['0.08', '8.00%', '0.08', '0.08', '0.08', '0.08']
    assert [cell['format_status'] for cell in cells] == ['raw', 'formatted', 'unsupported', 'unsupported', 'unsupported', 'unsupported']
    assert cells[0]['number_format'] == 'General'
    assert number('12', 'GENERAL')['format_status'] == 'raw'


def test_formula_cache_presence_is_distinct_from_cache_correctness_and_empty_values():
    rows = '''<row r="1">
      <c r="A1"><f>SUM(A2:A3)</f><v>9999</v></c>
      <c r="B1"><f>SUM(A2:A3)</f></c>
      <c r="C1"><f t="shared" si="0"/></c>
      <c r="D1" t="str"><f>IF(TRUE,"","x")</f><v/></c>
      <c r="E1"><f>1+1</f><v/></c>
      <c r="F1"><f>0</f><v>0</v></c>
      <c r="G1" t="e"><f>1/0</f><v>#DIV/0!</v></c>
      <c r="H1" t="b"><f>TRUE()</f><v>1</v></c>
      <c r="I1" t="str"><f>cmd|' /C should-never-execute'!A1</f><v>saved only</v></c>
      </row><row r="2"><c r="A2"><v>1</v></c></row><row r="3"><c r="A3"><v>2</v></c></row>'''
    result = xlsx_preview(workbook(rows, formats=['0.00'], calc='<calcPr calcMode="manual"/>'))
    sheet = result['sheets'][0]
    assert sheet['rows'][0] == ['9999.00', '=SUM(A2:A3)', '', '', '=1+1', '0.00', '#DIV/0!', 'TRUE', 'saved only']
    cells = sheet['cells'][0]
    assert all(cell['kind'] == 'formula' for cell in cells)
    assert [cell['formula_status'] for cell in cells] == ['saved', 'missing', 'missing', 'saved', 'missing', 'saved', 'saved', 'saved', 'saved']
    assert cells[3]['raw'] == '' and cells[5]['raw'] == '0'
    assert 'formula' not in cells[2]
    assert result['formula_results'] == 'not_recalculated' and result['calculation_warning'] is True
    assert not result['truncated'] and not sheet['truncated']


@pytest.mark.parametrize('calc', ['', '<calcPr calcMode="auto" fullCalcOnLoad="0"/>'])
def test_workbook_without_formulas_has_no_formula_claim(calc):
    result = xlsx_preview(workbook('<row><c><v>5</v></c></row>', calc=calc))
    assert 'formula_results' not in result and 'calculation_warning' not in result


def test_saved_formula_can_be_hidden_by_format_without_becoming_missing():
    result = xlsx_preview(workbook('<row><c><f>0</f><v>0</v></c></row>', formats=['0;-0;']))
    cell = result['sheets'][0]['cells'][0][0]
    assert cell['display'] == '' and cell['raw'] == '0' and cell['formula_status'] == 'saved'


def test_sparse_cells_text_booleans_errors_and_rows_are_aligned():
    rows = '<row r="7"><c r="C7" t="inlineStr"><is><r><t>Amount </t></r><r><t>&lt;b&gt;raw&lt;/b&gt;</t></r></is></c><c r="D7" t="s"><v>0</v></c><c r="E7" t="b"><v>0</v></c><c r="F7" t="e"><v>#N/A</v></c><c r="G7" t="d"><v>2026-10-05T12:00:00Z</v></c></row>'
    result = xlsx_preview(workbook(rows, extras={'xl/sharedStrings.xml': '<sst><si><t>Shared</t><r><t> text</t></r></si></sst>'}))
    sheet = result['sheets'][0]
    assert sheet['row_numbers'] == [7]
    assert sheet['rows'] == [['', '', 'Amount <b>raw</b>', 'Shared text', 'FALSE', '#N/A', '2026-10-05T12:00:00Z']]
    assert sheet['rows'] == [[cell['display'] for cell in row] for row in sheet['cells']]
    assert [cell['kind'] for cell in sheet['cells'][0]] == ['text', 'text', 'text', 'text', 'boolean', 'error', 'date']


@pytest.mark.parametrize(('rows', 'numbers'), [
    ('<row><c r="B7"><v>42</v></c></row>', [7]),
    ('<row><c r="B7"><v>42</v></c><c r="C7"><v>43</v></c></row>', [7]),
    ('<row r="7"><c r="A7"><v>1</v></c></row><row><c><v>2</v></c></row>', [7, 8]),
    ('<row><c><v>1</v></c></row><row r="7"/><row><c r="B42"><v>2</v></c></row><row><c><v>3</v></c></row>', [1, 7, 42, 43]),
    ('<row r="7"><c><v>1</v></c><c r="B7"><v>2</v></c><c><v>3</v></c></row>', [7]),
    ('<row><c r="B1048576"><v>42</v></c></row>', [1048576]),
])
def test_optional_row_coordinates_follow_explicit_cells_then_previous_row(rows, numbers):
    data = workbook(rows)
    original = hashlib.sha256(data).digest()
    result = xlsx_preview(data)
    assert result['sheets'][0]['row_numbers'] == numbers
    assert not result['truncated'] and not result['sheets'][0]['truncated']
    assert len(json.dumps(result, ensure_ascii=False)) <= MAX_CHARS
    assert hashlib.sha256(data).digest() == original


@pytest.mark.parametrize('rows', [
    '<row r="7"><c r="B8"><v>42</v></c></row>',
    '<row><c r="A7"><v>1</v></c><c r="B8"><v>2</v></c></row>',
    '<row r="7"><c r="A7"><v>1</v></c><c r="AO8"><v>2</v></c></row>',
    '<row r="7"/><row r="7"><c><v>42</v></c></row>',
    '<row r="7"/><row><c r="B6"><v>42</v></c></row>',
    '<row r="0"><c><v>42</v></c></row>',
    '<row r="invalid"><c r="B7"><v>42</v></c></row>',
    '<row><c r=""><v>42</v></c></row>',
    '<row><c r="B0"><v>42</v></c></row>',
    '<row><c r="B1048577"><v>42</v></c></row>',
    '<row r="1048576"/><row><c><v>42</v></c></row>',
])
def test_conflicting_or_invalid_coordinates_never_fabricate_addresses(rows):
    with pytest.raises(ValueError, match='worksheet'):
        xlsx_preview(workbook(rows))


def test_preview_budget_includes_raw_formula_format_metadata_and_repeated_display():
    value = '字"\\' * 650
    rows = ''.join(f'<row r="{index}"><c r="A{index}" t="str"><f>{escape(value)}</f><v>{escape(value)}</v></c></row>' for index in range(1, 200))
    data = workbook(rows, sheets=6, calc='<calcPr fullCalcOnLoad="1"/>')
    original = hashlib.sha256(data).hexdigest()
    result = xlsx_preview(data)
    assert result['truncated']
    assert 0 < len(result['sheets'][0]['rows']) < 199
    assert len(json.dumps(result, ensure_ascii=False)) <= MAX_CHARS
    assert result['sheets'][0]['rows'] == [[cell['display'] for cell in row] for row in result['sheets'][0]['cells']]
    assert hashlib.sha256(data).hexdigest() == original


def test_existing_cell_row_sheet_limits_and_truncated_original_fields():
    data = workbook(''.join(f'<row r="{index}"><c r="A{index}"><v>{index}</v></c></row>' for index in range(1, 202)), sheets=7)
    result = xlsx_preview(data)
    assert result['truncated'] and len(result['sheets']) == 6
    assert all(len(sheet['rows']) == 200 and sheet['truncated'] for sheet in result['sheets'])
    result = xlsx_preview(workbook('<row><c r="AN1"><v>40</v></c><c r="AO1"><v>41</v></c></row>'))
    assert len(result['sheets'][0]['rows'][0]) == 40 and result['sheets'][0]['truncated']
    result = xlsx_preview(workbook(f'<row><c t="str"><f>{"x" * 3000}</f><v>{"a" * 3000}</v></c></row>'))
    cell = result['sheets'][0]['cells'][0][0]
    assert all(len(cell[key]) == 2000 for key in ('raw', 'display', 'formula'))
    assert result['sheets'][0]['truncated']
    result = xlsx_preview(workbook('<row><c><v>3</v></c></row>', formats=['0' * 256]))
    assert result['sheets'][0]['truncated']
    assert result['sheets'][0]['cells'][0][0]['format_status'] == 'unsupported'


@pytest.fixture
async def client(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            assert (await client.post('/api/auth/register', json={'username': 'xlsx-reader', 'password': 'synthetic-only-password'})).status_code == 200
            session = (await client.post('/api/sessions', json={})).json()['id']
            yield client, session


async def test_http_quotation_preview_preserves_download_and_ownership(client):
    client, session = client
    data = workbook('<row r="1"><c r="A1"><v>1234.5</v></c><c r="B1" s="1"><v>0.08</v></c><c r="C1" s="2"><v>46299</v></c><c r="D1"><f>A1*B1</f><v>98.76</v></c></row>', formats=['"USD "#,##0.00', '0%', 'yyyy-mm-dd'])
    upload = await client.post(f'/api/sessions/{session}/files', params={'name': 'quotation.xlsx'}, content=data)
    assert upload.status_code == 201
    record = upload.json()
    response = await client.get(record['preview_url'])
    assert response.status_code == 200
    result = response.json()
    assert result['sheets'][0]['rows'] == [['USD 1,234.50', '8%', '2026-10-04', 'USD 98.76']]
    assert result['formula_results'] == 'not_recalculated'
    download = await client.get(record['content_url'], params={'download': 1})
    assert download.content == data and 'attachment;' in download.headers['content-disposition']
    await client.post('/api/auth/logout')
    assert (await client.get(record['preview_url'])).status_code == 401
    await client.post('/api/auth/register', json={'username': 'other-reader', 'password': 'synthetic-only-password'})
    assert (await client.get(record['preview_url'])).status_code == 404


def unsupported_archives():
    yield workbook('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><worksheet>&e;</worksheet>'.encode('utf-16'))
    yield workbook(('<worksheet>' + '<x>' * 65 + '</x>' * 65 + '</worksheet>').encode())
    yield workbook(('<worksheet>' + '<x/>' * 60001 + '</worksheet>').encode())
    yield workbook(b'a' * (4 * 1024 * 1024 + 1))
    data = bytearray(workbook('<row><c><v>1</v></c></row>'))
    # Mark all members as unsupported compression, covering ZipFile.open errors.
    for signature, offset in [(b'PK\x03\x04', 8), (b'PK\x01\x02', 10)]:
        index = data.find(signature)
        while index >= 0:
            struct.pack_into('<H', data, index + offset, 99)
            index = data.find(signature, index + 4)
    yield bytes(data)


async def test_unsafe_and_unreadable_archives_are_downloadable_without_preview_errors(client):
    client, session = client
    for data in unsupported_archives():
        record = (await client.post(f'/api/sessions/{session}/files', params={'name': 'unsupported.xlsx'}, content=data)).json()
        response = await client.get(record['preview_url'])
        assert response.status_code == 200 and response.json() == {'kind': 'unsupported'}
        assert (await client.get(record['content_url'])).content == data
