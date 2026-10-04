import io
import struct
import zipfile

import pytest

from athena.document_preview import docx_preview, MAX_OUTPUT_CHARS, MAX_XML_BYTES, MAX_XML_NODES
from test_files import file_client, upload

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def document(body='', *, extra=None, raw=None, namespace=W):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', raw if raw is not None else f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>')
        for path, content in (extra or {}).items():
            archive.writestr(path, content)
    return result.getvalue()


def paragraph(text, properties=''):
    return f'<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>'


async def test_docx_preview_preserves_body_heading_tables_and_original(file_client):
    client, _, session = file_client
    styles = f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Section"><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style><w:style w:type="paragraph" w:styleId="Custom"><w:basedOn w:val="Section"/></w:style></w:styles>'
    data = document(
        paragraph('Revenue &amp; costs', '<w:pPr><w:pStyle w:val="Custom"/></w:pPr>')
        + '<w:p><w:r><w:t>First line</w:t><w:br/><w:t>Next</w:t><w:tab/><w:t>column</w:t></w:r></w:p>'
        + '<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        + paragraph('Category') + '</w:tc></w:tr><w:tr><w:tc>' + paragraph('Materials')
        + '</w:tc><w:tc>' + paragraph('180') + '</w:tc></w:tr></w:tbl>', extra={'word/styles.xml': styles})
    record = (await upload(client, session, '预算.docx', data)).json()
    response = await client.get(record['preview_url'])
    assert response.status_code == 200
    preview = response.json()
    assert preview['kind'] == 'document' and not preview['truncated']
    assert preview['blocks'][:2] == [
        {'kind': 'paragraph', 'text': 'Revenue & costs', 'heading': 2},
        {'kind': 'paragraph', 'text': 'First line\nNext\tcolumn', 'heading': 0},
    ]
    rows = preview['blocks'][2]['rows']
    assert rows[0]['header'] is True and rows[0]['cells'][0]['colspan'] == 2
    assert rows[1]['cells'][1]['blocks'][0]['text'] == '180'
    original = await client.get(record['content_url'], params={'download': 1})
    assert original.content == data
    assert original.headers['content-disposition'].startswith('attachment;')


def test_strict_namespace_headings_nested_tables_and_content_controls():
    body = '<w:sdt><w:sdtPr/><w:sdtContent>' + paragraph('Strict Heading', '<w:pPr><w:pStyle w:val="Heading3"/></w:pPr>') + '</w:sdtContent></w:sdt>'
    body += '<w:tbl><w:tr><w:tc>' + paragraph('Outer') + '<w:tbl><w:tr><w:tc>' + paragraph('Inner') + '</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>'
    preview = docx_preview(document(body, namespace='http://purl.oclc.org/ooxml/wordprocessingml/main'))
    assert preview['blocks'][0]['heading'] == 3
    cell = preview['blocks'][1]['rows'][0]['cells'][0]
    assert cell['blocks'][1]['rows'][0]['cells'][0]['blocks'][0]['text'] == 'Inner'


def test_external_relationships_macros_fields_and_deleted_text_are_not_loaded(monkeypatch):
    extra = {
        'word/_rels/document.xml.rels': '<Relationship TargetMode="External" Target="https://example.invalid/track"/>',
        'word/vbaProject.bin': b'not executable',
        'word/embeddings/object.bin': b'embedded object',
        'word/media/image.png': b'not an image',
    }
    body = '<w:p><w:hyperlink><w:r><w:t>External label</w:t></w:r></w:hyperlink><w:del><w:r><w:t>deleted</w:t></w:r></w:del><w:r><w:instrText>DDEAUTO unsafe-command</w:instrText><w:t> cached result</w:t><w:drawing><w:t>image text</w:t></w:drawing></w:r></w:p><w:altChunk/>'
    body += '<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden</w:t></w:r><w:r><w:rPr><w:vanish w:val="false"/></w:rPr><w:t>visible</w:t></w:r></w:p>'
    data = document(body, extra=extra)
    opened = []
    original = zipfile.ZipFile.open
    def read(self, name, *args, **kwargs):
        opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', read)
    preview = docx_preview(data)
    assert preview['blocks'][0]['text'] == 'External label cached result'
    assert preview['blocks'][1]['text'] == 'visible'
    assert opened == ['word/document.xml']


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-be'])
async def test_doctype_entities_rejected_for_all_supported_xml_encodings(file_client, encoding):
    client, _, session = file_client
    raw = f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE document [<!ENTITY x "expanded secret">]><w:document xmlns:w="{W}"><w:body>{paragraph("&x;")}</w:body></w:document>'.encode(encoding)
    data = document(raw=raw)
    record = (await upload(client, session, 'unsafe.docx', data)).json()
    assert (await client.get(record['preview_url'])).json() == {'kind': 'unsupported'}
    assert (await client.get(record['content_url'])).content == data


@pytest.mark.parametrize('raw', [b'not a zip', document(raw=b'not XML'), document(raw=b'<wrong/>'), document(raw=b'<a>' + b'x' * MAX_XML_BYTES + b'</a>')])
async def test_bad_or_oversized_docx_remains_downloadable(file_client, raw):
    client, _, session = file_client
    record = (await upload(client, session, 'bad.docx', raw)).json()
    assert (await client.get(record['preview_url'])).json() == {'kind': 'unsupported'}
    assert (await client.get(record['content_url'])).content == raw


def test_xml_node_and_depth_budgets():
    with pytest.raises(ValueError, match='structure'):
        docx_preview(document('<w:sdt>' * 65 + paragraph('deep') + '</w:sdt>' * 65))
    with pytest.raises(ValueError, match='structure'):
        docx_preview(document('<w:p>' + '<w:r/>' * MAX_XML_NODES + '</w:p>'))


def test_archive_directory_is_bounded_before_zip_metadata_is_constructed(monkeypatch):
    data = struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 3000, 3000, 0, 0, 0)
    monkeypatch.setattr(zipfile, 'ZipFile', lambda *_: pytest.fail('Oversized directory reached ZipFile'))
    with pytest.raises(ValueError, match='directory'):
        docx_preview(data)


def test_duplicate_xml_entries_and_archive_expansion_are_rejected():
    data = io.BytesIO(document(paragraph('First')))
    with zipfile.ZipFile(data, 'a') as archive, pytest.warns(UserWarning):
        archive.writestr('word/document.xml', '<ambiguous/>')
    with pytest.raises(ValueError, match='ambiguous'):
        docx_preview(data.getvalue())
    with pytest.raises(ValueError, match='large'):
        docx_preview(document(paragraph('Body'), extra={'word/media/bomb.bin': b'x' * (33 * 1024 * 1024)}))


async def test_large_docx_clips_preview_but_preserves_entire_original(file_client):
    client, _, session = file_client
    data = document(paragraph('A' * (MAX_OUTPUT_CHARS + 20)) + paragraph('Ending preserved in original'))
    record = (await upload(client, session, 'long.docx', data)).json()
    preview = (await client.get(record['preview_url'])).json()
    assert preview['truncated']
    assert sum(len(block['text']) for block in preview['blocks']) == MAX_OUTPUT_CHARS
    assert (await client.get(record['content_url'])).content == data


def test_paragraph_and_table_limits_report_partial_preview():
    preview = docx_preview(document(paragraph('Item') * 1001))
    assert len(preview['blocks']) == 1000 and preview['truncated']
    table = '<w:tbl>' + ('<w:tr><w:tc>' + paragraph('Cell') + '</w:tc></w:tr>') * 201 + '</w:tbl>'
    preview = docx_preview(document(table))
    assert len(preview['blocks'][0]['rows']) == 200 and preview['truncated']
    table = '<w:tbl><w:tr>' + ('<w:tc>' + paragraph('Cell') + '</w:tc>') * 41 + '</w:tr></w:tbl>'
    preview = docx_preview(document(table))
    assert len(preview['blocks'][0]['rows'][0]['cells']) == 40 and preview['truncated']
