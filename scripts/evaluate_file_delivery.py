"""Opt-in model acceptance of a synthetic quotation, in a disposable account.

Run with uv run --directory backend python ../scripts/evaluate_file_delivery.py
  --key-file /path/to/private/key.txt --output /private/tmp/athena-file-acceptance.json
Only the provider call is remote. No existing users, browser profiles or files are
read. Receipts contain synthetic replies for semantic review, never API keys or
raw provider requests. Automated checks do not grade the meaning of prose.
"""
import argparse
import asyncio
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import time
import uuid
import zipfile
from xml.sax.saxutils import escape, quoteattr

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'backend'))

import httpx
from athena.api import create_app
from athena.config import Config


def quotation():
    """Valid synthetic OOXML; no dependency on installed Office or cached data."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    formats = ['0.00', '"¥"#,##0.00', '0%', 'yyyy-mm-dd', '0;-0;;@', '0.00" m²"', '0.0" h"']
    def cell(ref, value, style=None, formula=None, kind=None):
        attrs = f' r="{ref}"' + (f' s="{style}"' if style is not None else '') + (f' t="{kind}"' if kind else '')
        if kind == 'inlineStr':
            body = f'<is><t>{escape(value)}</t></is>'
        else:
            body = (f'<f>{escape(formula)}</f>' if formula is not None else '')
            body += f'<v>{escape(str(value))}</v>' if value is not None else ''
        return f'<c{attrs}>{body}</c>'
    def label(ref, value):
        return cell(ref, value, kind='inlineStr')
    rows = [
        [label(f'{col}1', value) for col, value in zip('ABCD', ['项目', '数量', '单价', '金额'])],
        [label('A2', '墙面材料'), cell('B2', '12.5', 5), cell('C2', '80', 1), cell('D2', '1000', 1, 'B2*C2')],
        [label('A3', '人工'), cell('B3', '8', 6), cell('C3', '120', 1), cell('D3', '960', 1, 'B3*C3')],
        [label('A4', '税率'), cell('B4', '0.08', 2)],
        [label('A5', '小计'), cell('D5', '1960', 1, 'SUM(D2:D3)')],
        [label('A6', '含税合计'), cell('D6', '2116.8', 1, 'D5*(1+B4)')],
        [label('A7', '报价日期'), cell('B7', '46299', 3)],
        [label('A8', '待计算示例'), cell('D8', None, 1, 'SUM(E2:E3)')],
        [label('A9', '隐藏零值示例'), cell('D9', '0', 4)],
        [label('A10', '保存空值示例'), cell('D10', '', formula='IF(TRUE,"","x")', kind='str')],
    ]
    assert (Decimal('12.5') * 80 + 8 * Decimal('120')) * Decimal('1.08') == Decimal('2116.80')
    rels = 'http://schemas.openxmlformats.org/package/2006/relationships'
    docrels = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' + ''.join(f'<Override PartName="/xl/{path}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml"/>' for path, kind in [('workbook.xml', 'sheet.main'), ('styles.xml', 'styles'), ('worksheets/sheet1.xml', 'worksheet')]) + '</Types>',
        '_rels/.rels': f'<Relationships xmlns="{rels}"><Relationship Id="rId1" Type="{docrels}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="{docrels}"><workbookPr date1904="0"/><sheets><sheet name="报价验收" sheetId="1" r:id="rId1"/></sheets><calcPr fullCalcOnLoad="1"/></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{rels}"><Relationship Id="rId1" Type="{docrels}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{docrels}/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml': f'<styleSheet xmlns="{ns}"><numFmts count="{len(formats)}">' + ''.join(f'<numFmt numFmtId="{164+i}" formatCode={quoteattr(code)}/>' for i, code in enumerate(formats)) + '</numFmts><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' + f'<cellXfs count="{len(formats)}">' + ''.join(f'<xf numFmtId="{164+i}" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' for i in range(len(formats))) + '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{ns}"><sheetData>' + ''.join(f'<row r="{i+1}">' + ''.join(row) + '</row>' for i, row in enumerate(rows)) + '</sheetData></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + body)
    return output.getvalue()


async def evaluate(args):
    output = args.output.expanduser().resolve()
    if output == REPO or REPO in output.parents:
        raise ValueError('Acceptance receipts must stay outside the checkout')
    if output.exists():
        raise FileExistsError('Choose a new receipt path before starting a paid evaluation')
    key = args.key_file.expanduser().read_text().strip()
    data = quotation()
    report = {'scope': 'isolated synthetic file task; no browser or physical-device acceptance', 'model': args.model,
              'input_variant': 'explicit_cell_states' if args.inspect_states else 'original_copy_request'}
    with TemporaryDirectory(prefix='athena-file-delivery-') as temporary:
        app = create_app(Config(data_dir=Path(temporary), repo_dir=REPO, api_key=key, model=args.model, max_steps=12))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317', timeout=30) as client:
                response = await client.post('/api/auth/register', json={'username': 'file-acceptance', 'password': uuid.uuid4().hex})
                response.raise_for_status()
                response = await client.post('/api/sessions', json={}); response.raise_for_status()
                session = response.json()['id']
                response = await client.post(f'/api/sessions/{session}/files', params={'name': '报价预览验收.xlsx'}, content=data)
                response.raise_for_status(); source = response.json()
                prompt = '报价预览验收（全部为合成数据）：请核对附件的材料费、人工费、税率与含税合计，把原文件原样复制为“报价验收结果.xlsx”，并在当前对话展示。只做本地文件处理，不要改设置、访问外部网站或发送给他人。遇到无法确认的值要说清楚。'
                if args.inspect_states:
                    prompt += '另外，D8、D9、D10 看起来都是空的，请分别说明它们实际上存了什么，能否据此认为计算结果都是零？也请给出报价日期及它所在的单元格。'
                start = time.monotonic()
                response = await client.post(f'/api/sessions/{session}/messages', json={'text': prompt, 'file_ids': [source['id']], 'client_submission_id': uuid.uuid4().hex, 'expected_submission_revision': 0})
                response.raise_for_status(); run = response.json()['run_id']
                timed_out = False
                while True:
                    response = await client.get(f'/api/sessions/{session}'); response.raise_for_status(); snapshot = response.json()
                    state = next(item for item in snapshot['runs'] if item['id'] == run)
                    if state['status'] not in ('queued', 'running', 'waiting_agents'):
                        break
                    if time.monotonic() - start > args.timeout:
                        timed_out = True
                        await app.state.engine.stop(session)
                        break  # Stop this original task; never resubmit after a timeout.
                    await asyncio.sleep(.1)
                report.update(status=state['status'], timed_out=timed_out, duration_seconds=round(time.monotonic()-start, 3))
                response = await client.get(source['content_url']); response.raise_for_status()
                checks = {'source_unchanged': response.content == data, 'run_complete': state['status'] == 'complete' and not timed_out}
                copied = next((file for file in snapshot['files'] if file['name'] == '报价验收结果.xlsx'), None)
                checks['output_registered'] = copied is not None
                if copied:
                    response = await client.get(copied['content_url']); response.raise_for_status()
                    checks['output_bytes_identical'] = response.content == data
                checks['no_redundant_component'] = not snapshot['components']
                history = await app.state.store.history(session)
                calls = {}
                for row in history:
                    for call in json.loads(row['tool_calls'] or '[]'):
                        try:
                            body = json.loads(call['function']['arguments'])
                        except (ValueError, TypeError):
                            body = {}
                        arguments = body.get('args') or body
                        calls[call['id']] = {'tool': call['function']['name'], 'op': body.get('op'), 'action': arguments.get('action'),
                                             **({'name': arguments['name']} if body.get('op') == 'skill' and 'name' in arguments else {})}
                errors, failures = 0, []
                for row in history:
                    if row['role'] == 'tool':
                        try:
                            value = json.loads(row['content'])
                            failed = bool(value.get('error') or value.get('error_code') or value.get('exit_code', 0))
                            errors += int(failed)
                            if failed:
                                # Categorize failures without retaining shell text/output or credentials.
                                diagnostic = str(value.get('output') or '')
                                exception = re.findall(r'\b(ModuleNotFoundError|ImportError|SyntaxError|NameError|KeyError|IndexError|FileNotFoundError|PermissionError|TypeError|ValueError):', diagnostic)
                                missing = re.search(r"No module named ['\"]([A-Za-z0-9_.]+)['\"]", diagnostic)
                                failures.append({**calls.get(row['tool_call_id'], {}), 'exit_code': value.get('exit_code'),
                                                 'error_code': value.get('error_code'), 'error_type': type(value.get('error')).__name__,
                                                 'exception_class': exception[-1] if exception else None,
                                                 'missing_module': missing.group(1) if missing else None,
                                                 'command_not_found': 'command not found' in diagnostic})
                        except (ValueError, TypeError, AttributeError):
                            pass
                requests = await app.state.store.rows('SELECT usage FROM requests WHERE run_id=?', (run,))
                totals = {key: 0 for key in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens')}
                for row in requests:
                    usage = json.loads(row['usage'] or '{}')
                    for key in totals:
                        totals[key] += usage.get(key, 0)
                checks['canonical_file_read'] = any(call['op'] == 'files' and call['action'] == 'read' for call in calls.values())
                checks['no_tool_errors'] = errors == 0
                reply = '\n'.join(row['content'] for row in history if row['role'] == 'assistant' and row['content'])
                checks['no_decorative_emoji'] = not re.search('[\U0001f300-\U0001faff\u2705\u274c\u26a0\ufe0f]', reply)
                report.update(checks=checks, automated_passed=all(checks.values()), requests=len(requests), tool_calls=len(calls), tool_errors=errors,
                              usage=totals, actions=list(calls.values()), failures=failures, input_sha256=hashlib.sha256(data).hexdigest(),
                              reply=reply[-8000:],
                              semantic_review_required=True,
                              semantic_expectations=['material=1000; labor=960; subtotal=1960; tax=8%; total=2116.8',
                                                     'D8 missing cache is not blank or zero', 'D9 stores zero hidden by formatting',
                                                     'D10 has a saved empty-string result; saved caches are not fresh recalculation evidence',
                                                     'B7 is the quotation date 2026-10-04'])
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2); handle.write('\n')
    print(json.dumps({key: value for key, value in report.items() if key not in ('reply', 'semantic_expectations')}, ensure_ascii=False))
    return report['automated_passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--inspect-states', action='store_true', help='Also ask for the three distinct apparent-empty cell states')
    options = parser.parse_args()
    if not 1 <= options.timeout <= 600:
        parser.error('--timeout must be between 1 and 600 seconds')
    sys.exit(0 if asyncio.run(evaluate(options)) else 1)
